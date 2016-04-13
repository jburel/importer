import os
import glob
from celery.decorators import task

import omero
import omero.cli

def empty_temp(dir_path):
    for old_file in os.listdir(dir_path):
        file_path = os.path.join(dir_path, old_file)
        os.unlink(file_path)

def get_new_image(conn, tempdir):
    """ 
    Retrieved the ID of the new image from stdout.
    
    @param conn: The BlitzGateway connection
    """    
    log = glob.glob(tempdir + '/stdout.txt')
    with open(log[0],'r') as f:
        ids = f.readlines()
        
    image_id = int(ids[0])
    newImg = conn.getObject('Image',image_id)
    return newImg

@task(name="omero import")
def celery_import(conn, tempdir, filename, groupId, datasetId):
    conn.SERVICE_OPTS.setOmeroGroup(groupId)
    user = conn.getUser()
    #sessionId = session['ID']
    sessionId = conn.c.getSessionId()

    cli = omero.cli.CLI()
    cli.loadplugins()
    cli.invoke(["sessions", "login", "-s", "localhost", "-k", "%s" % sessionId], strict=True)
    cli.invoke(["sessions", "group", "%s" % groupId], strict=True)    
    import_args = ["import"]
    import_args.extend(["-d", str(datasetId)])
    import_args.append(filename)
    import_args.extend(["-s","localhost","-u","%s"%user.getName()])
    
    # redirect both stderr and stdout to file
    errlog = tempdir + "/stderr.txt"
    import_args.extend(["---errs",errlog])
    outlog = tempdir + "/stdout.txt"
    import_args.extend(["---file",outlog])
    cli.invoke(import_args, strict=True)
    
    # use stdout to get the id of the new image
    newImg = get_new_image(conn,tempdir)
    empty_temp(tempdir)
    return newImg