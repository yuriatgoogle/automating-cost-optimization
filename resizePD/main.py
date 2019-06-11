# for execution in Cloud Functions Python 3.7.1

# modify these variables for your environment:
project = 'automating-cost-optimization'
authorizedUsername = "postman"
authorizedPassword = "postman"

# imports
import datetime
import googleapiclient
from googleapiclient import discovery
from oauth2client.client import GoogleCredentials
from datetime import datetime
import calendar
import time
import sys
from flask import request
from flask import Flask
from flask import escape
from basicauth import decode

# initialize global
compute = googleapiclient.discovery.build('compute', 'v1')
credentials = GoogleCredentials.get_application_default()
zone = ''
vm = ''
snapShotId = ''
newDiskId = ''

# define helper functions

def authorizeRequest (request):
    encoded_str = request.headers.get('Authorization')
    username, password = decode(encoded_str)
    #TODO update username and password
    if (username == authorizedUsername, password == authorizedPassword):
        return True
    return False

def waitForZoneOperation(operationResponse, project, zone):
    status = operationResponse["status"]
    name = operationResponse["name"]
    while (status != "DONE"):
        checkRequest = compute.zoneOperations().get(project=project, operation=name, zone=zone)
        checkResponse = checkRequest.execute()
        status = checkResponse["status"]
        time.sleep(3)

# main function
def resizePD(request):
    
    # Authorize incoming request 
    if (authorizeRequest(request)==False):
        print ("Unauthorized request")
        return ("Unauthorized request")
    print ("authorized request")

    # process incoming body 
    request_json = request.get_json(force=True)
    id = request_json['incident']['resource_id']

    # get aggregated VM list and get our VM
    listRequest = compute.instances().aggregatedList(project=project, filter='id={}'.format(id))
    while listRequest is not None:
        listResponse = listRequest.execute()
        for name, instances_scoped_list in listResponse['items'].items():
            if instances_scoped_list.get('warning') is None:
                # there are instances
                for instance in instances_scoped_list['instances']: # iterate through all instances in zone
                    if instance['id'] == id: # should be only one match
                        instanceName = instance['name']
                        print ("instance name is" + instanceName) # instance name
                        vm = instanceName
                        zone = name.rsplit('/',1)[1] # zone name
                        print ("zone is " + zone)
                        print  ('Instance name is: {}\n'.format(instanceName))
        listRequest = compute.instances().aggregatedList_next(previous_request=listRequest, previous_response=listResponse)
    
    # generate timestamped values
    d = datetime.utcnow()
    unixtime = calendar.timegm(d.utctimetuple())

    
    # get the VM in question
    vmGetRequest = compute.instances().get(project=project, zone=zone, instance=vm)
    vmGetResponse = vmGetRequest.execute()
    instance = vmGetResponse["name"]
 
    # get second disk - do not operate on boot disk
    currentDiskSource = vmGetResponse["disks"][1]["source"]
    diskDeviceName = vmGetResponse["disks"][1]["deviceName"]
    currentDisk = currentDiskSource.rsplit('/', 1)[-1]
    
    # fetch the disk
    print("fetching disk")
    diskGetRequest = compute.disks().get(project=project, zone=zone, disk=currentDisk)
    diskGetResponse = diskGetRequest.execute()
    print("got disk: " + diskGetResponse["name"])
    originalDiskSize = diskGetResponse["sizeGb"]
    print ("original disk size is: " + str(originalDiskSize))

    newSnapshotName = diskDeviceName + str(unixtime)
    newDiskName = newSnapshotName

    # create the snapshot
    print('taking snapshot ' + newSnapshotName)
    snapshot_body = {
        "name" : newSnapshotName
    }
    snapResponse = (compute.disks().createSnapshot(project=project, zone=zone, disk=currentDisk, body=snapshot_body)).execute()
    print('took snapshot: ' + newSnapshotName)
    print('operation status is: ' + str(snapResponse["status"]))
    waitForZoneOperation(snapResponse, project, zone)
    snapShotId = snapResponse["id"]
    print("snapshot completed. snapshot: " + snapShotId)

    # get the snapshot self-link with snapshots.list
    snapListRequest = (compute.snapshots().list(project=project, filter=("name=" + newSnapshotName)))
    snapShotLink = ''
    while snapListRequest is not None:
        snapListResponse = snapListRequest.execute()
        for snapshot in snapListResponse['items']:
            # should have only one response
            print (snapshot['selfLink'])
            snapShotLink = snapshot['selfLink']
            snapListRequest = compute.snapshots().list_next(previous_request=snapListRequest, previous_response=snapListResponse)

    # create new disk from snapshot using link (not ID)
    print("creating new disk from snapshot")
    # set size for new disk
    newDiskSize = int(float(originalDiskSize) - 1) # reduce disk by 1
    disk_body = {
        "name" : newDiskName,
        "sizeGb" : newDiskSize,
        "sourceSnapshot" : snapShotLink
    }
    diskCreateResponse = (compute.disks().insert(project=project, zone=zone, body=disk_body)).execute() 
    print("started disk creation")
    waitForZoneOperation(diskCreateResponse, project, zone)
    newDiskId = diskCreateResponse["targetLink"]
    print("disk creation complete: " + newDiskId)

    # stop VM if it's running
    # check if the machine is running first
    print("getting VM status")
    vmStatus = vmGetResponse["status"]
    if (vmStatus == 'RUNNING'): # if machine is running, stop it
        print("stopping vm")
        stopResponse = (compute.instances().stop(project=project, zone=zone, instance=vm)).execute()
        waitForZoneOperation(stopResponse, project, zone)
        print("stopped VM")
    else:
        print("vm is already stopped") # if not running, we're done
    
    # detach current boot disk
    print("detaching existing disk: " + currentDisk)
    detachResponse = (compute.instances().detachDisk(project=project, zone=zone, instance=vm, deviceName=diskDeviceName)).execute()
    waitForZoneOperation(detachResponse, project, zone)
    print("detached disk")

    # attach new disk
    print("attaching new disk")
    attachBody = {
        "boot" : "false",
        "source" : newDiskId
    }
    attachResponse = (compute.instances().attachDisk(project=project, zone=zone, instance=vm, body=attachBody)).execute()
    waitForZoneOperation(attachResponse, project, zone)
    print("attached new disk")

    # start the VM
    print("starting VM")
     # check if the machine is running first
    vmGetReponse = (compute.instances().get(project=project, zone=zone, instance=vm)).execute()
    print("getting VM status")
    vmStatus = vmGetReponse["status"]
    if (vmStatus != 'RUNNING'): 
        print("vm is not running")
        startResponse = (compute.instances().start(project=project, zone=zone, instance=vm)).execute()
        waitForZoneOperation(startResponse, project, zone)
        print("started VM")
    else:
        print("vm is already running") # if running, we're done

    return ("PD resized!")

