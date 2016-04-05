import glob
import os
import shutil
import tempfile
from datetime import datetime
import unicodedata
import json
import time
from cStringIO import StringIO

from django.conf import settings
from django.http import Http404, HttpResponse, HttpResponseRedirect, HttpResponseBadRequest
from django.shortcuts import render
from django.core.urlresolvers import reverse

from forms import UploadForm, GroupForm, ProjectForm, DatasetForm, FeedbackForm
from tasks import celery_import

import omero
import omero.cli
from omero.rtypes import wrap, rlong, rstring
from omero.gateway import OriginalFileWrapper
from omeroweb.webclient.decorators import login_required, render_response

TEMP_DIR = '/home/omero/temp/'
JSON_FILEANN_NS = "omero.web.incident.json"

def createOriginalFileFromFileObj(
        conn, fo, path, name, fileSize, mimetype=None, ns=None):
    """
    This is a copy of the same method from Blitz Gateway, but fixes a bug
    where the conn.SERVICE_OPTS are not passed in the API calls.
    Once this is fixed in OMERO-5 (and we don't need to work with OMERO-4)
    then we can revert to using the BlitzGateway for this method again.
    """
    rawFileStore = conn.createRawFileStore()

    # create original file, set name, path, mimetype
    originalFile = omero.model.OriginalFileI()
    originalFile.setName(rstring(name))
    originalFile.setPath(rstring(path))
    if mimetype:
        originalFile.mimetype = rstring(mimetype)
    originalFile.setSize(rlong(fileSize))
    # set sha1 # ONLY for OMERO-4
    try:
        import hashlib
        hash_sha1 = hashlib.sha1
    except:
        import sha
        hash_sha1 = sha.new
    try:
        fo.seek(0)
        h = hash_sha1()
        h.update(fo.read())
        shaHast = h.hexdigest()
        originalFile.setSha1(rstring(shaHast))
    except:
        pass       # OMERO-5 doesn't need this
    upd = conn.getUpdateService()
    originalFile = upd.saveAndReturnObject(originalFile, conn.SERVICE_OPTS)

    # upload file
    fo.seek(0)
    rawFileStore.setFileId(originalFile.getId().getValue(), conn.SERVICE_OPTS)
    buf = 10000
    for pos in range(0, long(fileSize), buf):
        block = None
        if fileSize-pos < buf:
            blockSize = fileSize-pos
        else:
            blockSize = buf
        fo.seek(pos)
        block = fo.read(blockSize)
        rawFileStore.write(block, pos, blockSize, conn.SERVICE_OPTS)
    # https://github.com/openmicroscopy/openmicroscopy/pull/2006
    originalFile = rawFileStore.save(conn.SERVICE_OPTS)
    rawFileStore.close()
    return OriginalFileWrapper(conn, originalFile)

def empty_temp(dir_path):
    for old_file in os.listdir(dir_path):
        file_path = os.path.join(dir_path, old_file)
        os.unlink(file_path)

def get_new_image(conn):
    """ 
    Retrieved the ID of the new image from stdout.
    
    @param conn: The BlitzGateway connection
    """    
    log = glob.glob(TEMP_DIR + '/stdout.txt')
    with open(log[0],'r') as f:
        ids = f.readlines()
        
    image_id = int(ids[0])
    newImg = conn.getObject('Image',image_id)
    return newImg

def list_object_ids(objects):
    olist = []
    for o in objects:
        olist.append(o['id'])
    return olist

def list_object_names(objects):
    olist = []
    for o in objects:
        print "o",o
        olist.append(o['name'])
    return olist

def list_datasets(conn,gid,project_id):
    conn.SERVICE_OPTS.setOmeroGroup(gid)
    project = conn.getObject("Project", project_id)
    datasets = []
    for d in project.listChildren():
        ddata = {'id': d.getId(), 'name': d.getName()}
        ddata['description'] = d.getDescription()
        ddata['owner'] = d.getDetails().getOwner().getOmeName()
        datasets.append(ddata)
    
    return datasets
    
def list_projects(conn,gid):

    conn.SERVICE_OPTS.setOmeroGroup(gid)
    user = conn.getUser()
    user_id = user.getId()
    projects = []
    # Will be from active group, owned by user_id (as perms allow)
    for p in conn.listProjects(eid=user_id):
        pdata = {'id': p.getId(), 'name': p.getName()}
        pdata['description'] = p.getDescription()
        pdata['owner'] = p.getDetails().getOwner().getOmeName()
        projects.append(pdata)

    return projects

def list_groups(conn):

    ctx = conn.getEventContext()
    myGroups = list(conn.getGroupsMemberOf())

    user = conn.getUser()
    user_id = user.getId()

    # Need a custom query to get 1 (random) image per Project
    queryService = conn.getQueryService()
    params = omero.sys.ParametersI()
    params.theFilter = omero.sys.Filter()
    params.theFilter.limit = wrap(1)

    query = "select count(obj.id) from %s as obj"

    groups = []
    for g in myGroups:
        conn.SERVICE_OPTS.setOmeroGroup(g.id)
        images = list(conn.getObjects("Image", params=params))
        if len(images) == 0:
            continue        # Don't display empty groups
        pCount = queryService.projection(query % 'Project', None, conn.SERVICE_OPTS)
        dCount = queryService.projection(query % 'Dataset', None, conn.SERVICE_OPTS)
        iCount = queryService.projection(query % 'Image', None, conn.SERVICE_OPTS)
        groups.append({'id': g.getId(),
                'name': g.getName(),
                'description': g.getDescription(),
                'projectCount': pCount[0][0]._val,
                'datasetCount': dCount[0][0]._val,
                'imageCount': iCount[0][0]._val,
                'image': len(images) > 0 and images[0] or None})  

        # need to get groups, all projects in each group and then all datasets in each project
    return groups
    
def do_import(conn, filename):
    """
    Import the new image to OMERO using the command line importer
    
    @param conn: The BlitzGateway connection
    @param session: A dictionary containing the session ID and hostname
    @param filename: The path of the image being imported
    @param dataset: The dataset into which the new image is being placed
    @param project: The project into which the dataset is being placed
    """
    user = conn.getUser()
    #sessionId = session['ID']
    sessionId = conn.c.getSessionId()
    cli = omero.cli.CLI()
    cli.loadplugins()
    cli.invoke(["sessions", "login", "-s", "localhost", "-k", "%s" % sessionId], strict=True)
    import_args = ["import"]
    dsId = 1 # should be feedback DS of public data
    import_args.extend(["-d", str(dsId)])
    import_args.append(filename)
    import_args.extend(["-s","localhost","-u","%s"%user.getName()])
    
    # redirect both stderr and stdout to file
    errlog = TEMP_DIR + "/stderr.txt"
    import_args.extend(["---errs",errlog])
    outlog = TEMP_DIR + "/stdout.txt"
    import_args.extend(["---file",outlog])
    cli.invoke(import_args, strict=True)
    
    # use stdout to get the id of the new image
    newImg = get_new_image(conn)
    empty_temp(TEMP_DIR)
    return newImg
    
@login_required()
@render_response()
def upload(request, conn=None, **kwargs):
    
    if request.POST:  
        form = UploadForm(request.POST, request.FILES)
        if form.is_valid():  
            uploaded_file = request.FILES['file']  
            chunk = request.POST['chunk']  
            chunks = request.POST['chunks']   
            name = request.POST['name'] 
            
            #tempdir = tempfile.mkdtemp(prefix='/home/omero/temp/')
            temp_file = os.path.join(TEMP_DIR, name)  
            with open(temp_file, ('wb' if chunk == '0' else 'ab')) as f:  
               for content in uploaded_file.chunks():  
                   f.write(content)  

            if int(chunk) + 1 >= int(chunks):  
                #form.save(temp_file, name)  
                # instead of saving trigger cli importer
                img = celery_import(conn,TEMP_DIR,temp_file)
                #img = do_import(conn,temp_file)
                
            if request.is_ajax():  
                response = HttpResponse('{"jsonrpc" : "2.0", "result" : null, "id" : "id"}')  
                response['Expires'] = 'Mon, 1 Jan 2000 01:00:00 GMT'  
                response['Cache-Control'] = 'no-store, no-cache, must-revalidate, post-check=0, pre-check=0'  
                response['Pragma'] = 'no-cache'  
                return response  
            else:  
                return HttpResponseRedirect(reverse('report')) 
        else:
            response_data = {'form_saved': False, 'errors': form.errors}
            return HttpResponse(json.dumps(response_data))
    else:
        user = conn.getUser()
        groups = list_groups(conn)
        group_names = list_object_names(groups)
        gids = list_object_ids(groups)
        gnames = []

        first_group = groups[0]
        projects = list_projects(conn,first_group['id'])
        project_names = list_object_names(projects)
        pids = list_object_ids(projects)
        pnames = []

        first_project = projects[0]
        datasets = list_datasets(conn,first_group['id'],first_project['id'])
        dataset_names = list_object_names(datasets)
        dids = list_object_ids(datasets)
        dnames = []        

        for gn,g in zip(group_names,gids):
            gnames.append((g,gn))

        for pn in project_names:
            pnames.append((pn,pn))

        for dn in dataset_names:
            dnames.append((dn,dn))


        uform = UploadForm()
        gform = GroupForm(groups=gnames)
        pform = ProjectForm(projects=pnames)
        dform = DatasetForm(datasets=dnames)
        context = {}
        context['upload_form'] = uform
        context['group_form'] = gform         
        context['project_form'] = pform        
        context['dataset_form'] = dform                
        context['page_size'] = settings.PAGE
        context['template'] = 'omeroweb_upload/index.html'
        return context

@login_required()
def listDatasets_json(request, conn=None, **kwargs):

    if request.POST:
        gid = request.POST.get("group_id")
        pid = request.POST.get("project_id")
        rv = list_datasets(conn,gid,pid)
        data = json.dumps(rv)
        return HttpResponse(data, content_type='application/json')
    else:
        rv = {'message':"failed"}
        error = json.dumps(rv)
        return HttpResponseBadRequest(error, content_type='application/json')
            
@login_required()
def listProjects_json(request, conn=None, **kwargs):

    if request.POST:
        gid = request.POST.get("group_id")
        print "gid",gid
        rv = list_projects(conn,gid)
        data = json.dumps(rv)
        return HttpResponse(data, content_type='application/json')
    else:
        rv = {'message':"failed"}
        error = json.dumps(rv)
        return HttpResponseBadRequest(error, content_type='application/json')

# a view to be called from uploader when all files are completed
# perhaps keep the image_ids in request.session
@login_required()    
def send_message(request,conn=None):
    """
    E-mail the result to the user.

    @param conn: The BlitzGateway connection
    @param params: The script parameters
    @param image_ids: A python list of the new image omero ids
    """
    if not params['Email_Results']:
        return

    image_names = list_image_names(conn, image_ids)

    msg = MIMEMultipart()
    msg['From'] = ADMIN_EMAIL
    msg['To'] = params['Email_address']
    msg['Date'] = formatdate(localtime=True)
    msg['Subject'] = 'FEEDBACK'
    msg.attach(MIMEText("""
New user feedback:

Format:
[parent project/datset] image id : image name

------------------------------------------------------------------------
%s""" % ("\n".join(image_names))))

    smtpObj = smtplib.SMTP('localhost')
    smtpObj.sendmail(ADMIN_EMAIL, [params['Email_address']], msg.as_string())
    smtpObj.quit()

