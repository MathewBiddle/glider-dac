import os
from flask.ext.mail import Message
from flask import render_template
from glider_dac.defaults import MAIL_ENABLED, PUBLIC_ERDDAP, THREDDS
from glider_dac import app, mail

def send_wmoid_email(username, deployment):
    if not MAIL_ENABLED: # Mail is disabled
        app.logger.info("Email is disabled")
        return
    # sender comes from MAIL_DEFAULT_SENDER in env
    app.logger.info("Sending email about new deployment to %s", app.config.get('MAIL_DEFAULT_TO'))
    subject        = "New Glider Deployment - %s" % deployment.name
    recipients     = [app.config.get('MAIL_DEFAULT_TO')]
    cc_recipients  = []
    if app.config.get('MAIL_DEFAULT_LIST') is not None:
        cc_recipients.append(app.config.get('MAIL_DEFAULT_LIST'))

    msg            = Message(subject, recipients=recipients, cc=cc_recipients)
    msg.body       = render_template(
                        'wmoid_email.txt', 
                        deployment=deployment, 
                        username=username, 
                        thredds_url=get_thredds_catalog_url(), 
                        erddap_url=get_erddap_catalog_url())

    mail.send(msg)

def get_thredds_catalog_url():
    args = {
        'host' : THREDDS
    }
    url = u'http://%(host)s/thredds/catalog.xml' % args
    return url

def get_erddap_catalog_url():
    args = {
        'host' : PUBLIC_ERDDAP
    }
    url = u'http://%(host)s/erddap/metadata/iso19115/xml/' % args
    return url
