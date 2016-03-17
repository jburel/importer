import glob
import os
import shutil
import tempfile
from datetime import datetime
import unicodedata
import json
import time
from cStringIO import StringIO

from django.http import Http404, HttpResponse, HttpResponseRedirect, HttpResponseBadRequest
from django.shortcuts import render
from django.core.urlresolvers import reverse

from forms import UploadForm, FeedbackForm
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

def index(request):
    """
    Just a place-holder while we get started
    """
    return HttpResponse("Welcome to your app home-page!")

def list_incidents(conn=None, **kwargs):

    fileAnns = list(conn.getObjects(
        "FileAnnotation", attributes={'ns': JSON_FILEANN_NS}))
    #fileAnns.sort(key=lambda x: x.creationEventDate(), reverse=True)
    if fileAnns:
        rsp = []
        for fa in fileAnns:

            incidentJSON = "".join(list(fa.getFileInChunks()))
            incidentJSON = incidentJSON.decode('utf8')
            jsonFile = fa.getFile()
            ownerId = jsonFile.getDetails().getOwner().getId()

            # parse the json, so we can add info...
            json_data = json.loads(incidentJSON)
            json_data['fileId'] = fa.id
            rsp.append(json_data)
        return rsp
    #else:
    #    response_data = {'message': "no incidents"}
    #    return HttpResponse(json.dumps(response_data))

    
@login_required()
@render_response()
def report(request, conn=None, **kwargs):
    
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
        print user.omeName
        form = UploadForm()
        incidents = list_incidents(conn)
        context = {}
        context['form'] = form
        context['incidents'] = incidents
        context['template'] = 'omeroweb_upload/containers.html'
        return context
    
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

@login_required(setGroupContext=True)
def save_incident(request, conn=None, **kwargs):
    """
    Saves 'incidentJSON' in POST as an original file. If 'fileId' is specified
    in POST, then we update that file. Otherwise create a new one with
    name 'figureName' from POST.
    """

    update = conn.getUpdateService()
    if not request.method == 'POST':
        return HttpResponse("Need to use POST")

    incidentJSON = request.POST.get('incidentJSON')
    if incidentJSON is None:
        return HttpResponse("No 'incidentJSON' in POST")

    incidentJSON = incidentJSON.encode('utf8')

    json_data = json.loads(incidentJSON)

    n = datetime.now( )
    # time-stamp name by default: WebFigure_2013-10-29_22-43-53.json
    incidentName = "Incident_%s-%s-%s_%s-%s-%s.json" % \
        (n.year, n.month, n.day, n.hour, n.minute, n.second)
    incidentName = incidentName.encode('utf8')
    # we store json in description field...
    description = {}
    description['name'] = incidentName

    gid = conn.getGroupFromContext().getId()
    conn.SERVICE_OPTS.setOmeroGroup(gid)

    fileSize = len(incidentJSON)
    f = StringIO()
    f.write(incidentJSON)
    # Can't use unicode for file name
    incidentName = unicodedata.normalize('NFKD', unicode(incidentName)).encode('ascii','ignore')
    origF = createOriginalFileFromFileObj(
        conn, f, '', incidentName, fileSize, mimetype="application/json")
    fa = omero.model.FileAnnotationI()
    fa.setFile(omero.model.OriginalFileI(origF.getId(), False))
    fa.setNs(wrap(JSON_FILEANN_NS))
    desc = json.dumps(description)
    fa.setDescription(wrap(desc))
    fa = update.saveAndReturnObject(fa, conn.SERVICE_OPTS)
    fileId = fa.id.val
    rv = json_data
    rv['fileId'] = fileId
    data = json.dumps(rv)
    return HttpResponse(data, content_type='application/json')

@login_required()
def delete_incident(request, conn=None, **kwargs):
    """ POST 'fileId' to delete the FileAnnotation """

    if request.method != 'POST':
        return HttpResponse("Need to POST 'fileId' to delete")

    fileId = request.POST.get('fileId')
    # fileAnn = conn.getObject("FileAnnotation", fileId)
    conn.deleteObjects("Annotation", [fileId])
    return HttpResponse(str(fileId))

