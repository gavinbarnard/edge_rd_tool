#!/usr/bin/python
# edge redeploy tool v 0.3 grb June 18 2016
# 
# v 0.1 milestones:
# 	> fetch XML > COMPLETE
#	> build XML API calls to rebuild > COMPLETE
# 	> send XML api calls to build > COMPLETE
#	
#	testing:
#	> 15 attempts and much tweaking successful redeploy of original edge-20.xml with minimal firewall dhcp routing ha nat config
#
# v 0.2 milestones:
#	> expand and configure everything in test edge and redeploy 
# 	> tested OSPF, IPSEC, DNS to minimal config > COMPLETE
#	> IPSEC PSK is LOST needs manual reset or edit of xml by hand before -R step edit at your own peril
#	> tested minimal load balancer, syslog, LARGE size config > COMPLETE
#	> added howto/example
#	> ssl_vpn works but any localusers are lost / need to be recreated
#	> either ther api is broken for adding/creating all users
# 	> or i can't figure it out :(
# 	> minimal config tested shortlist: ipsec,firewall,routing,Syslog,dhcp,lb,ha,nat,dns,subinterfaces,l2vpn
#       > sub interfaces may havebroken HA
#	> they did... would need a rewrite to force rebuild order to be HA > subinterfaces > l2Vpn as rebuild order atm
#	> added exclude subInterface option for -B
#	
# v 0.3 milestones: June 19 2016
#	> removed exclude subInterface option
#	> rewrite rebuild order > COMPLETE > pending handler for subInterface > COMPETE
#	> rewrite subInterface to secondary file posted after HA > COMPLETE
#	> expand testing to large firewall rule sets (200/500/1000) see if it needs to be broken up into multiple puts # tested with up to 328 rules
#	# see if we can fix ssl_vpn users? > WONTFIX
# 	
#
# howto:  Get > Rebuild API calls > Build;   The Rebuild, and Build step will state wether a component is enabled or successfully configured (204)
#       Get XML Config of existing edge that needs clean redeploy
# 	./edge_rd_tool.py -G edge-20 -n nsxmanager -u admin -p default > edge-20.xml
#
#	Rebuild/chop the configs up into smaller API POST/PUTs; creates/overwrites rebuild_<name>.xml* files.
#	./edge_rd_tool.py -B edge-20.xml
#
#	Build the edge with rebuild_<name>.xml and rebuild_<name>.xml.<feature> files
#	./edge_rd_tool.py -R rebuild_edge-20.xml -n nsxmanager -u admin -p default 
#  
# example:
#  gavin@ssh:~/edge$ ./edge_rd_tool.py -G edge-20 -n valscar -u admin -p default > edge-20.xml
#
#  gavin@ssh:~/edge$ ./edge_rd_tool.py -B edge-20.xml
#  rebuilding: edge-20
#  named: TestyMcEdge
#  features:
#  l2Vpn not enabled
#  firewall enabled
#  sslvpnConfig not enabled
#  dns enabled
#  routing enabled
#  highAvailability enabled
#  syslog enabled
#  loadBalancer enabled
#  gslb not enabled
#  ipsec enabled
#  dhcp enabled
#  nat enabled
#  bridges not enabled
#
#  gavin@ssh:~/edge$ ls rebuild_* edge-20.xml
#  edge-20.xml               rebuild_edge-20.xml.dns               rebuild_edge-20.xml.ipsec         rebuild_edge-20.xml.routing
#  rebuild_edge-20.xml       rebuild_edge-20.xml.firewall          rebuild_edge-20.xml.loadBalancer  rebuild_edge-20.xml.syslog
#  rebuild_edge-20.xml.dhcp  rebuild_edge-20.xml.highAvailability  rebuild_edge-20.xml.nat
#
#  gavin@ssh:~/edge$ ./edge_rd_tool.py -R rebuild_edge-20.xml -n nsxmanager -u admin -p default
#  Got new Edge API Path: /api/4.0/edges/edge-38
#  Sending ipsec config
#  204
#  Sending Firewall config
#  204
#  Sending routing config
#  204
#  Sending dhcp config
#  204
#  Sending LB config
#  204
#  Sending HA config
#  204
#  Sending nat config
#  204
#  Sending Dns config
#  204
#
#
#
# BEWARE - THERE BE DRAGONS!!

import sys, requests, getopt, glob
import xml.etree.ElementTree as ET

from requests.packages.urllib3.exceptions import InsecureRequestWarning

requests.packages.urllib3.disable_warnings(InsecureRequestWarning)

# Define functions used by the script


def nsxgetrest ( uri, username, passwd ):
	"This function makes an NSX REST call with the GET method"
	r = requests.get(uri, auth=(username,passwd), headers={'Content-Type': 'application/xml','Accept': "application/xml"}, verify=False)
	return r

def nsxpmrest ( uri, username, passwd, payload, method ):
	"This function makes an NSX REST call with a payload using the specified method"
	if method == "post" or method == "POST":
		r = requests.post(uri, auth=(username,passwd), headers={'Content-Type': 'application/xml','Accept': "application/xml"}, data = payload, verify=False)
	elif method == "put" or method == "PUT":
		r = requests.put(uri, auth=(username,passwd), headers={'Content-Type': 'application/xml','Accept': "application/xml"}, data = payload, verify=False)
	elif method =="delete" or method == "DELETE":
		r = requests.delete(uri, auth=(username,passwd), headers={'Content-Type': 'application/xml','Accept': "application/xml"}, verify=False)
	else:
		return false
	return r
def loadfeaturefile (feature):
	"This function loads a file"
	rbef_feature_file = open (feature,'r')
	payload_data = rbef_feature_file.read()
	rbef_feature_file.close()
	return payload_data	

def print_my_help ():
	"This function prints my help message"
	print "edge_rd_tool.py -hGBRDs -n <nsx_fqdn> -u <username> -p <password>"
	print 
	print " -h : this help "
	print " -G <edgeid>         | --edge <edgeid> "
	print " 	gets the Edge XML from NSX Manager"
	print "		redirect output to a file to process with the -B option"
	print
	print " -B <edgexmlfile>    | --edgexml <edgexmlfile> "
	print "		creates a Rebuild XML, will emit messages indicating each section that"
	print "		needs an additional REST call "
	print "		::warning the Edge name will be changed from <name> to <name>_rebuild"
	print "		::warning the Edge password will be changed to Default12!Default12!"
	print "		::warning to change either of these values manually edit the xml files are your own peril"
	print "		::warning IPSEC / SSL VPN PSKs will be lost and literally changed to *'s"
	print "		::warning SSL VPN local users are lost due to an API issue"
	print 
	print "		a new xml file will be created named rebuild_<xmlfilename>"
	print "		other files may be generated/overwritten depending if features are enabled or not"
	print "		naming format will be rebuild_<xmlfilename>.<featurename>"
	print "		Example if you specify 'edge-20.xml' as the <edgexmlfile>"
	print "		the following files will appear:"
	print "			rebuild_edge-20.xml"
	print "			rebuild_edge-20.xml.firewall"
	print "			rebuild_edge-20.xml.dns"
	print 
	print " -R <rebuildxmlfile> | --rebuild <rebuildxmlfile> "
	print " 	sends NSX REST calls to NSX Manager to recreate the Edge"
 	print
	print " -D <edgeapipath>"
	print "		deletes an Edge"
	print 
	print "-n <nsx_fqdn>"
	print "		NSX manager to communicate with"
	print 
	print "-u <username>"
	print "		Username to use for NSX Manager communication"
	print 
	print "-p <password>"
	print "		Password to use for NSX Manager communication"
	print "Options n, u, p are only required for G, R, and D"
	

#Parse options / Print Help Message
try:
	opts, args = getopt.getopt(sys.argv[1:],"hG:B:R:D:n:u:p:",['edge=','edgexml=','rebuild='])
except getopt.GetoptError:
	print_my_help()
	sys.exit(2)

if len(opts) == 0:
	print_my_help()
	sys.exit(2)

for opt, arg in opts:
	if opt in ("-G","--edge"):
		my_edge = arg
		my_action = 1
	elif opt in ("-B","--edgexml"):
		my_edge_xml_file = arg
		my_action = 2
	elif opt in ("-R","--rebuild"):
		my_edge_rebuild_file_name = arg
		my_action = 3
	elif opt == "-D":
		my_edge_api_path = arg
		my_action = 4
	elif opt == '-u':
		my_username = arg
	elif opt == '-n':
		my_nsxmanager = arg
	elif opt == '-p':
		my_password = arg
	elif opt == '-h':
		print_my_help()
		sys.exit(2)

if my_action == 1 or my_action == 3 or my_action == 4:
	paramTest = 0
	if 'my_username' not in locals():
		print "Error: Missing Username (-u <username>)"
		paramTest = 1
	if 'my_nsxmanager' not in locals():
		print "Error: Missing NSX Manager (-n <nsxmanager_fqdn>)"
		paramTest = 1
	if 'my_password' not in locals():
		print "Error: Missing Password (-p <password>)"
		paramTest = 1
	if paramTest == 1:
		print_my_help()
		sys.exit(2)

# Simply get and output the Edge XML data from NSX Manager
if my_action == 1:
	uri = "https://" + my_nsxmanager + "/api/4.0/edges/" + my_edge
	edge_request =  nsxgetrest(uri,my_username,my_password)	
	print edge_request.content

if my_action == 4:
	if my_edge_api_path.find("edge-") == -1:
		print "edge-id not found"
		print_my_help()
		sys.exit(2)

	uri = "https://" + my_nsxmanager + my_edge_api_path
	edge_request = nsxpmrest(uri,my_username,my_password,"","DELETE")
	print edge_request.status_code
	if edge_request.content is not None:
		print edge_request.content
	
# Parse an existing Edge definition and recreate seperate XML files 
# to rebuild the edge, and reconfigure it's features
# if a tag is not processed
# it either caused problems on rebuild (such as id/version/names of objects)
# or is not part of the API doc nsx_62_api.pdf
# to rebuild an edge you must first post the base information, I've encountered issues when sending PUTs for "ALL" edge config
# either due to size of request or certain features erroring our due to "interface" configuration not being in place
# and then make PUT statements to update the <features> sections returned by the GET above
# 


elif my_action == 2:
	rbef = ET.Element("edge")
	edge_xml_tree = ET.parse(my_edge_xml_file)
	exroot = edge_xml_tree.getroot()
	for child in exroot:
		if child.tag == 'id':
			print "rebuilding: " + child.text
		if child.tag == 'datacenterMoid':
			xdcMoid = ET.SubElement(rbef,'datacenterMoid')
			xdcMoid.text = child.text

		if child.tag == 'tenant':
			xtenant = ET.SubElement(rbef,'tenant')
			xtenant.text = child.text

		if child.tag == 'name':
			xname = ET.SubElement(rbef,'name')
			xname.text = child.text+"_rebuild"
			print "named: " + child.text

		if child.tag == 'fqdn':
			xfqdn = ET.SubElement(rbef,'fqdn')
			xfqdn.text = child.text

		if child.tag == 'enableAesni':
			xaes = ET.SubElement(rbef,'enableAesni')
			xaes.text = child.text

		if child.tag == 'enableFips':
			xfips = ET.SubElement(rbef,'enableFips')
			xfips.text = child.text

		if child.tag == 'vseLogLevel':
			xlogl = ET.SubElement(rbef,'vseLogLevel')
			xlogl.text = child.text

		if child.tag == 'vnics':
			xvnics = ET.SubElement(rbef,"vnics")
			for vnic in child:
				if vnic.tag == "vnic" and vnic.find("./subInterfaces") is None: 
					xvnic = ET.SubElement(xvnics,"vnic")
					for vChild in vnic:
						if vChild.tag == 'label':
							xvnicl = ET.SubElement(xvnic,"label")
							xvnicl.text = vChild.text
						if vChild.tag == 'name':
							xvnicname = ET.SubElement(xvnic,"name")
							xvnicname.text = vChild.text
						if vChild.tag == 'mtu':
							xvnicmtu = ET.SubElement(xvnic,"mtu")
							xvnicmtu.text = vChild.text
						if vChild.tag == 'type':
							xvnictype = ET.SubElement(xvnic,"type")
							xvnictype.text = vChild.text
						if vChild.tag == "isConnected":
							xvniciscon = ET.SubElement(xvnic,"isConnected")
							xvniciscon.text = vChild.text
						if vChild.tag == "index":
							xvnicindex = ET.SubElement(xvnic,"index")
							xvnicindex.text = vChild.text
						if vChild.tag == "portgroupId":
							xvnicpg = ET.SubElement(xvnic,"portgroupId")
							xvnicpg.text = vChild.text
						if vChild.tag == "enableProxyArp":
							xvnicepa = ET.SubElement(xvnic,"enableProxyArp")
							xvnicepa.text = vChild.text
						if vChild.tag == "enableSendRedirects":
							xvnicesr = ET.SubElement(xvnic,"enableSendRedirects")
							xvnicesr.text = vChild.text
						if vChild.tag == "addressGroups":
							xvnicags = ET.SubElement(xvnic,"addressGroups")
							for ags in vChild:
								xvnicag = ET.SubElement(xvnicags,"addressGroup")
								for ag in ags:
									if ag.tag == "primaryAddress":
										xvnicagpa = ET.SubElement(xvnicag,"primaryAddress")
										xvnicagpa.text = ag.text
									if ag.tag == "subnetMask":
										xvnicagsm = ET.SubElement(xvnicag,"subnetMask")
										xvnicagsm.text = ag.text
									if ag.tag == "subnetPrefixLength":
										xvnicagpl = ET.SubElement(xvnicag,"subnetPrefixLength")
										xvnicagpl.text = ag.text
									if ag.tag == 'secondaryAddresses':
										xvnicagsa = ET.SubElement(xvnicag,"secondaryAddresses")
										for ipaddr in ag:
											if ipaddr.tag == 'ipAddress':
												xvnicagsaip = ET.SubElement(xvnicagsa,"ipAddress")
												xvnicagsaip.text = ipaddr.text
				if vnic.tag == "vnic" and vnic.find("./subInterfaces") is not None:
					xvnic = ET.Element("vnic")
					for vChild in vnic:
						if vChild.tag == 'label':
							xvnicl = ET.SubElement(xvnic,"label")
							xvnicl.text = vChild.text
						if vChild.tag == 'name':
							xvnicname = ET.SubElement(xvnic,"name")
							xvnicname.text = vChild.text
						if vChild.tag == 'mtu':
							xvnicmtu = ET.SubElement(xvnic,"mtu")
							xvnicmtu.text = vChild.text
						if vChild.tag == 'type':
							xvnictype = ET.SubElement(xvnic,"type")
							xvnictype.text = vChild.text
						if vChild.tag == "isConnected":
							xvniciscon = ET.SubElement(xvnic,"isConnected")
							xvniciscon.text = vChild.text
						if vChild.tag == "index":
							xvnicindex = ET.SubElement(xvnic,"index")
							xvnicindex.text = vChild.text
						if vChild.tag == "portgroupId":
							xvnicpg = ET.SubElement(xvnic,"portgroupId")
							xvnicpg.text = vChild.text
						if vChild.tag == "enableProxyArp":
							xvnicepa = ET.SubElement(xvnic,"enableProxyArp")
							xvnicepa.text = vChild.text
						if vChild.tag == "enableSendRedirects":
							xvnicesr = ET.SubElement(xvnic,"enableSendRedirects")
							xvnicesr.text = vChild.text
						if vChild.tag == "subInterfaces":
							xvnicsis = ET.SubElement(xvnic,"subInterfaces")
							for vnicsis in vChild:
								if vnicsis.tag == "subInterface":
									xvnicsi = ET.SubElement(xvnicsis,"subInterface")
									for vnicsi in vnicsis:
										if vnicsi.tag == "isConnected":
											xvnicsicon = ET.SubElement(xvnicsi,"isConnected")
											xvnicsicon.text = vnicsi.text
										if vnicsi.tag =="label":
											xvnicsilab = ET.SubElement(xvnicsi,"label")
											xvnicsilab.text = vnicsi.text
										if vnicsi.tag =="name":
											xvnicsinam = ET.SubElement(xvnicsi,"name")
											xvnicsinam.text = vnicsi.text
										if vnicsi.tag =="tunnelId":
											xvnicsitun = ET.SubElement(xvnicsi,"tunnelId")
											xvnicsitun.text = vnicsi.text
										if vnicsi.tag =="mtu":
											xvnicsimtu = ET.SubElement(xvnicsi,"mtu")
											xvnicsimtu.text = vnicsi.text
										if vnicsi.tag =="vlanId":
											xvnicsivln = ET.SubElement(xvnicsi,"vlanId")
											xvnicsivln.text = vnicsi.text
										if vnicsi.tag =="enableSendRedirects":
											xvnicsiesr = ET.SubElement(xvnicsi,"enableSendRedirects")
											xvnicsiesr.text = vnicsi.text
										if vnicsi.tag =="addressGroups":
						                                        xvnicsiags = ET.SubElement(xvnicsi,"addressGroups")
		                                				        for ags in vnicsi:
					                        	                	xvnicsiag = ET.SubElement(xvnicsiags,"addressGroup")
		                        			        	                for ag in ags:
		                                                               				if ag.tag == "primaryAddress":
		                                                                        			xvnicsiagpa = ET.SubElement(xvnicsiag,"primaryAddress")
		                                                                        			xvnicsiagpa.text = ag.text
		                                                               				if ag.tag == "subnetMask":
		                                                                        			xvnicsiagsm = ET.SubElement(xvnicsiag,"subnetMask")
		                                                                        			xvnicsiagsm.text = ag.text
		                                                                			if ag.tag == "subnetPrefixLength":
		                                                                        			xvnicsiagpl = ET.SubElement(xvnicsiag,"subnetPrefixLength")
		                                                                        			xvnicsiagpl.text = ag.text
		                                                                			if ag.tag == 'secondaryAddresses':
		                                                                        			xvnicsiagsa = ET.SubElement(xvnicsiag,"secondaryAddresses")
		                                                                       				for ipaddr in ag:
		                                                                                			if ipaddr.tag == 'ipAddress':
		                                                                                   				xvnicsiagsaip = ET.SubElement(xvnicsiagsa,"ipAddress")
		                                                                                        			xvnicsiagsaip.text = ipaddr.text
						if vChild.tag == "addressGroups":
							xvnicags = ET.SubElement(xvnic,"addressGroups")
							for ags in vChild:
								xvnicag = ET.SubElement(xvnicags,"addressGroup")
								for ag in ags:
									if ag.tag == "primaryAddress":
										xvnicagpa = ET.SubElement(xvnicag,"primaryAddress")
										xvnicagpa.text = ag.text
									if ag.tag == "subnetMask":
										xvnicagsm = ET.SubElement(xvnicag,"subnetMask")
										xvnicagsm.text = ag.text
									if ag.tag == "subnetPrefixLength":
										xvnicagpl = ET.SubElement(xvnicag,"subnetPrefixLength")
										xvnicagpl.text = ag.text
									if ag.tag == 'secondaryAddresses':
										xvnicagsa = ET.SubElement(xvnicag,"secondaryAddresses")
										for ipaddr in ag:
											if ipaddr.tag == 'ipAddress':
												xvnicagsaip = ET.SubElement(xvnicagsa,"ipAddress")
												xvnicagsaip.text = ipaddr.text
					#
					rebuild_xmlstring = ET.tostring(xvnic)
					rebuild_file_name = "rebuild_" + my_edge_xml_file + ".subInterface." + xvnicindex.text
					rebuild_file = open(rebuild_file_name,"w")
					rebuild_file.write(rebuild_xmlstring)
					rebuild_file.close()
		if child.tag == 'appliances':
			xapps = ET.SubElement(rbef,"appliances")
			for appChild in child:
				if appChild.tag == 'applianceSize':
					xappSize = ET.SubElement(xapps,"applianceSize")
					xappSize.text = appChild.text
				if appChild.tag =='appliance':
					xapp = ET.SubElement(xapps,"appliance")
					for vappChild in appChild:
						if vappChild.tag == "resourcePoolId":
							xapprpid = ET.SubElement(xapp,"resourcePoolId")
							xapprpid.text = vappChild.text
						if vappChild.tag == "datastoreId":
							xappdsid = ET.SubElement(xapp,"datastoreId")
							xappdsid.text = vappChild.text
						if vappChild.tag == "vmFolderId":
							xappfldid = ET.SubElement(xapp,"vmFolderId")
							xappfldid.text = vappChild.text
		if child.tag == 'cliSettings':
			xcli = ET.SubElement(rbef,'cliSettings')
			for cliChild in child:
				if cliChild.tag == 'remoteAccess':
					xclira = ET.SubElement(xcli,"remoteAccess")
					xclira.text = cliChild.text
				if cliChild.tag == 'userName':
					xusername = ET.SubElement(xcli,"userName")
					xusername.text = cliChild.text
				if cliChild.tag == 'sshLoginBannerText':
					xsshbanner = ET.SubElement(xcli,"sshLoginBannerText")
					xsshbanner.text = cliChild.text
				if cliChild.tag == 'passwordExpiry':
					xpwexp = ET.SubElement(xcli,"passwordExpiry")
					xpwexp.text = cliChild.text

			xpassword = ET.SubElement(xcli,"password")
			xpassword.text = "Default12!Default12!"
		if child.tag == 'features':
			print "features:"
			# here we can almost cartblanche use the xml contexts returned
			# so we dump the XML string content and rebuild a new xml entity
			# and strip out the versions tag
			# firewall does not like this # now working with own handler
			# nat does not like this # now working with own handler
			# no docs for gslb  
			# l2vpn requires password blankin
			for features in child:
				clip_xmlstring = ET.tostring(features)
				rb_feat = ET.fromstring(clip_xmlstring)
				for ver in rb_feat.findall("version"):
					rb_feat.remove(ver)
				en = rb_feat.find("./enabled")
				if en is not None:
					if en.text == "true":
						print features.tag + " enabled"
						if features.tag == "nat" or features.tag == "firewall":
							rb_feat.remove(en)
							if features.tag == "firewall":
								rb_feat = ET.Element("firewall")
								for fwconfig in features:
									if fwconfig.tag == "globalConfig":
										fwgc = ET.SubElement(rb_feat,"globalConfig")
										for gcparams in fwconfig:
											gcparam = ET.SubElement(fwgc,gcparams.tag)
											gcparam.text = gcparams.text
									if fwconfig.tag == "defaultPolicy":
										dfp = ET.SubElement(rb_feat,"defaultPolicy")
										for dfparams in fwconfig:
											dfparam = ET.SubElement(dfp,dfparams.tag)
											dfparam.text = dfparams.text
									if fwconfig.tag == "firewallRules":
										xfwrs = ET.SubElement(rb_feat,"firewallRules")
										for fwrs in fwconfig:
											if fwrs.tag == "firewallRule":
												rt = fwrs.find("./ruleType")
												if not rt.text == "default_policy" and not rt.text == "internal_high" and not rt.text == "internal_low": 
													xfwr = ET.SubElement(xfwrs,"firewallRule")
													for fwr in fwrs: ## ruleTags from UI created are outside of the 1-65536 for "user" specified ruleTags ## TODO write test for ruleTag in user range
														if not fwr.tag == "ruleTag" and not fwr.tag == "id" and not fwr.tag == "ruleType" and not fwr.tag == "source" and not fwr.tag == "destination" and fwr.text is not None:
															xxtag = ET.SubElement(xfwr,fwr.tag)
															xxtag.text = fwr.text
														if fwr.tag == "source" or fwr.tag == "destination":
															xxtag = ET.SubElement(xfwr,fwr.tag)
															for sd in fwr:
																xxxtag = ET.SubElement(xxtag,sd.tag)
																xxxtag.text = sd.text
												
								rb_feat_xmlstring = ET.tostring(rb_feat)
	                                                        rb_feat_file_name = "rebuild_" + my_edge_xml_file + "." + features.tag
	                                                        rb_feat_file = open(rb_feat_file_name,'w')
        	                                                rb_feat_file.write(rb_feat_xmlstring)
                	                                        rb_feat_file.close()
							if features.tag == "nat":
								xnat = ET.Element("nat")
								rb_feat = ET.SubElement(xnat,"natRules")
								for natrules in features:
									if natrules.tag == "natRules":
											for natrule in natrules: ## TODO same as above for user range ruleTag
												if natrule.tag == "natRule":
													rt = natrule.find("./ruleType")
													if rt.text == "user":
														xnatrule = ET.SubElement(rb_feat,"natRule")
														for natruleopt in natrule:
															if not natruleopt.tag == "ruleId" and not natruleopt.tag == "ruleType" and not natruleopt.tag =="ruleTag" and natruleopt.text is not None:
																xxtag = ET.SubElement(xnatrule,natruleopt.tag)
																xxtag.text = natruleopt.text
								rb_feat_xmlstring = ET.tostring(xnat)
								rb_feat_file_name = "rebuild_" + my_edge_xml_file + "." + features.tag
								rb_feat_file = open(rb_feat_file_name,'w')
								rb_feat_file.write(rb_feat_xmlstring)
								rb_feat_file.close()
														
														
												
						else:				
							rb_feat_xmlstring = ET.tostring(rb_feat)
							rb_feat_file_name = "rebuild_" + my_edge_xml_file + "." + features.tag
							rb_feat_file = open(rb_feat_file_name,'w')
							rb_feat_file.write(rb_feat_xmlstring)
							rb_feat_file.close()
					else:
						print features.tag + " not enabled"
		if child.tag == 'autoConfiguration':
			xautoconfig = ET.SubElement(rbef,'autoConfiguration')
			for acChild in child:
				if acChild.tag == 'enabled':
					xacenabled = ET.SubElement(xautoconfig,"enabled")
					xacenabled.text = acChild.text

				if acChild.tag == 'rulePriority':
					xacrp = ET.SubElement(xautoconfig,"rulePriority")
					xacrp.text = acChild.text
	rebuild_xmlstring = ET.tostring(rbef)
	rebuild_file_name = "rebuild_" + my_edge_xml_file
	rebuild_file = open(rebuild_file_name,"w")
	rebuild_file.write(rebuild_xmlstring)
	rebuild_file.close()

elif my_action == 3:
	rbef_file = open(my_edge_rebuild_file_name,'r')
	rbef_payload_data = rbef_file.read()
	rbef_file.close()
	uri = "https://" + my_nsxmanager + "/api/4.0/edges"
	edge_request = nsxpmrest(uri,my_username,my_password,rbef_payload_data,"POST")
	edge_api_path = edge_request.headers.get("Location")
	if edge_api_path is None:
		print "failed to get edge api path, duplicate edge name may exist"
		sys.exit(2)

	print "Got new Edge API Path: " + edge_api_path
	feature_files = glob.glob(my_edge_rebuild_file_name + ".*")
	stripchr = len(my_edge_rebuild_file_name) + 1
	
	features = [f[stripchr:] for f in feature_files]

	if "routing" in features:
		payload_data = loadfeaturefile(my_edge_rebuild_file_name + ".routing")
		uri = "https://" + my_nsxmanager + edge_api_path + "/routing/config"
		print "Sending routing config"
		edge_request = nsxpmrest(uri,my_username,my_password,payload_data,"PUT")
		print edge_request.status_code
	if "firewall" in features:
		payload_data = loadfeaturefile(my_edge_rebuild_file_name + ".firewall")
		uri = "https://" + my_nsxmanager + edge_api_path + "/firewall/config"
		print "Sending Firewall config"
		edge_request = nsxpmrest(uri,my_username,my_password,payload_data,"PUT")
		print edge_request.status_code
	if "nat" in features:
		payload_data = loadfeaturefile(my_edge_rebuild_file_name + ".nat")
		uri = "https://" + my_nsxmanager + edge_api_path + "/nat/config"
		print "Sending nat config"
		edge_request = nsxpmrest(uri,my_username,my_password,payload_data,"PUT")
		print edge_request.status_code
	if "dhcp" in features:
		payload_data = loadfeaturefile(my_edge_rebuild_file_name + ".dhcp")
		uri = "https://" + my_nsxmanager + edge_api_path + "/dhcp/config"
		print "Sending dhcp config"
		edge_request = nsxpmrest(uri,my_username,my_password,payload_data,"PUT")
		print edge_request.status_code
	if "dns" in features:
		payload_data = loadfeaturefile(my_edge_rebuild_file_name + ".dns")
		uri = "https://" + my_nsxmanager + edge_api_path + "/dns/config"
		print "Sending Dns config"
		edge_request = nsxpmrest(uri,my_username,my_password,payload_data,"PUT")
		print edge_request.status_code
	if "syslog" in features:
		payload_data = loadfeaturefile(my_edge_rebuild_file_name + ".syslog")
		uri = "https://" + my_nsxmanager + edge_api_path + "/syslog/config"
		print "Sending Syslog config"
		edge_request = nsxpmrest(uri,my_username,my_password,payload_data,"PUT")
		print edge_request.status_code	
	if "loadBalancer" in features:
		payload_data = loadfeaturefile(my_edge_rebuild_file_name + ".loadBalancer")
		uri = "https://" + my_nsxmanager + edge_api_path + "/loadbalancer/config"
		print "Sending LB config"
		edge_request = nsxpmrest(uri,my_username,my_password,payload_data,"PUT")
		print edge_request.status_code
	if "ipsec" in features:
		payload_data = loadfeaturefile(my_edge_rebuild_file_name + ".ipsec")
		uri = "https://" + my_nsxmanager + edge_api_path + "/ipsec/config"
		print "Sending ipsec config"
		edge_request = nsxpmrest(uri,my_username,my_password,payload_data,"PUT")
		print edge_request.status_code
	if "highAvailability" in features:
		payload_data = loadfeaturefile(my_edge_rebuild_file_name + ".highAvailability")
		uri = "https://" + my_nsxmanager + edge_api_path + "/highavailability/config"
		print "Sending HA config"
		edge_request = nsxpmrest(uri,my_username,my_password,payload_data,"PUT")
		print edge_request.status_code
	for subInt in range(0,10):
		if "subInterface." + str(subInt) in features:
			uri = "https://" + my_nsxmanager + edge_api_path + "/vnics/" + str(subInt)
			payload_data = loadfeaturefile(my_edge_rebuild_file_name + ".subInterface."+str(subInt))
			print "Sending vNic_"+str(subInt)+" with subInterface(s)"
			edge_request = nsxpmrest(uri,my_username,my_password,payload_data,"PUT")
			print edge_request.status_code

	if "l2Vpn" in features:
		payload_data = loadfeaturefile(my_edge_rebuild_file_name + ".l2Vpn")
		uri = "https://" + my_nsxmanager + edge_api_path + "/l2vpn/config/"
                print "Sending l2Vpn config"
		# password *must* be posted 
		if payload_data.find("<password>") == -1:
			payload_data = payload_data.replace("</userId>","</userId><password>Default12!Default12!</password>")
		edge_request = nsxpmrest(uri,my_username,my_password,payload_data,"PUT")
		print edge_request.status_code
	if "bridges" in features:
		payload_data = loadfeaturefile(my_edge_rebuild_file_name + ".bridges")
		uri = "https://" + my_nsxmanager + edge_api_path + "/bridging/config"
		print "Sending bridging config"
		edge_request = nsxpmrest(uri,my_username,my_password,payload_data,"PUT")
		print edge_request.status_code
	if "sslvpnConfig" in features:
		payload_data = loadfeaturefile(my_edge_rebuild_file_name + ".sslvpnConfig")
		print "Sending sslvpn config"
		xsslvpnConfig = ET.fromstring(payload_data)
		xservsett = xsslvpnConfig.find("./serverSettings")
		if xservsett is not None: 
			payload_data = ET.tostring(xservsett)
			uri = "https://" + my_nsxmanager + edge_api_path + "/sslvpn/config/server/"
			print "		serverSettings"
			edge_request = nsxpmrest(uri,my_username,my_password,payload_data,"PUT")
			print edge_request.status_code
		xprivnet = xsslvpnConfig.find("./privateNetwork")
		if xprivnet is not None:
			payload_data = ET.tostring(xprivnet)
			uri = "https://" + my_nsxmanager + edge_api_path + "/sslvpn/config/client/networkextension/privatenetworks/"
			print "		privateNetwork"
			edge_request = nsxpmrest(uri,my_username,my_password,payload_data,"PUT")
			print edge_request.status_code
		xwebresource = xsslvpnConfig.find("./webResource")
		if xwebresource is not None:
			payload_data = ET.tostring(xwebresource)
			uri = "https://" + my_nsxmanager + edge_api_path + "/sslvpn/config/webresources/"
			print "		webResource"
			edge_request = nsxpmrest(uri,my_username,my_password,payload_data,"PUT")
			print edge_request.status_code
		xusers = xsslvpnConfig.find("./users") ## TODO FIX ME? 
		if xusers is not None:
			payload_data = ET.tostring(xusers)
			uri = "https://" + my_nsxmanager + edge_api_path + "/sslvpn/config/auth/localserver/users/"
			print "		users - BROKEN"
			edge_request = nsxpmrest(uri,my_username,my_password,payload_data,"PUT")
			print edge_request.status_code
		xippool = xsslvpnConfig.find("./ipAddressPools")
		if xippool is not None:
			payload_data = ET.tostring(xippool)
			uri = "https://" + my_nsxmanager + edge_api_path + "/sslvpn/config/client/networkextension/ippools"
			print "		ippool"
			edge_request = nsxpmrest(uri,my_username,my_password,payload_data,"PUT")
			print edge_request.status_code
		xcconf = xsslvpnConfig.find("./clientConfiguration")
		if xcconf is not None:
			payload_data = ET.tostring(xcconf)
			uri = "https://" + my_nsxmanager + edge_api_path + "/sslvpn/config/client/networkextension/clientconfig/"
			print "		clientConfiguration"
			edge_request = nsxpmrest(uri,my_username,my_password,payload_data,"PUT")
			print edge_request.status_code
		xcip = xsslvpnConfig.find("./clientInstallPackage")
		if xcip is not None:
			payload_data = ET.tostring(xcip)
			uri = "https://" + my_nsxmanager + edge_api_path + "/sslvpn/config/client/networkextension/installpackages"
			print " 		clientInstallPackage"
			edge_request = nsxpmrest(uri,my_username,my_password,payload_data,"PUT")
			print edge_request.status_code
		xlayout = xsslvpnConfig.find("./layoutConfiguration")
		if xlayout is not None:
			xml_string = ET.tostring(xlayout)
			payload_data = xml_string.replace("layoutConfiguration","layout")
			uri = "https://" + my_nsxmanager + edge_api_path + "/sslvpn/config/layout/portal"
			print "		layout"
			edge_request = nsxpmrest(uri,my_username,my_password,payload_data,"PUT")
			print edge_request.status_code

		 
