#!/usr/bin/env python

import time
import os.path
import os
import argparse
import glob
import logging
from datetime import datetime
from glider_dac import app, db
from watchdog.events import (FileSystemEventHandler,
                             DirCreatedEvent, DirDeletedEvent,
                             FileCreatedEvent, FileMovedEvent, FileModifiedEvent)
from watchdog.observers import Observer


class HandleDeploymentDB(FileSystemEventHandler):
    def __init__(self, base, flagsdir):
        self.base = base
        self.flagsdir = flagsdir  # path to ERDDAP flags folder

    def file_moved_or_created(self, event):
        app.logger.info('%s %s', self.base, event.src_path)
        if self.base not in event.src_path:
            return

        rel_path = os.path.relpath(event.src_path, self.base)
        if isinstance(event, FileMovedEvent):
            rel_path = os.path.relpath(event.dest_path, self.base)
        path_parts = os.path.split(rel_path)
        app.logger.info("%s %s", type(event), path_parts)

        # ignore if a dotfile
        if path_parts[1].startswith('.'):
            return

        with app.app_context():
            # navoceano unsorted deployments
            if path_parts[0] == "navoceano/hurricanes-20230601T0000":
                if not path_parts[-1].endswith(".nc"):
                    return
                deployment_name_raw, extension = os.path.splitext(path_parts[-1])
                try:
                    # NAVOCEANO uses underscore instead of dash for separating deployment name, this is fixed later
                    # when sylinking the deployment
                    glider_callsign, date_str_tz = deployment_name_raw.split("_", 1)
                except ValueError:
                    app.logger.exception("Cannot split NAVOCEANO hurricane glider filename into callsign and timestamp components: ")
                date_str = (date_str_tz[:-1] if date_str_tz.endswith("Z") else
                            date_str_tz)
                # remove trailing Z from deployment name
                navo_directory = os.path.join(self.base, "navoceano")
                navo_deployment_directory = None
                possible_existing_dirs = list(glob.iglob(os.path.join(navo_directory, f"{glider_callsign}*")))
                # Use an already existing directory if there is one for the the deployment
                # TODO: handle for mulitple possible existing callsigns if new deployment is made?
                for maybe_dir in possible_existing_dirs:
                    if os.path.isdir(maybe_dir):
                        navo_deployment_directory = maybe_dir
                # TODO: handle for mulitple possible existing callsigns if new deployment is made?
                        break
                # otherwise specify a dir to be created
                if not navo_deployment_directory:
                    navo_deployment_directory = os.path.join(self.base, f"navoceano/{glider_callsign}-{date_str}")
                # Directory could exist, but no deployment in DB!
                self.create_deployment_if_nonexistent(os.path.split(navo_deployment_directory))
                try:
                    os.makedirs(navo_deployment_directory,
                                exist_ok=True)
                    # should now fire another inotify filesystem event upon symlink creation
                    os.symlink(os.path.join(self.base, rel_path),
                               os.path.join(navo_deployment_directory, f"{glider_callsign}_{date_str}{extension}"))
                except OSError:
                    app.logger.exception("Could not create new deployment file for NAVOCEANO: ")
                return

            deployment = db.Deployment.find_one({'deployment_dir' : path_parts[0]})
            if deployment is None:
                app.logger.error("Cannot find deployment for %s", path_parts[0])
                return

            if path_parts[-1] == "wmoid.txt":
                rel_path = os.path.relpath(event.src_path, self.base)
                app.logger.info("New wmoid.txt in %s", rel_path)
                if deployment.wmo_id:
                    app.logger.info("Deployment already has wmoid %s.  Updating value with new file.", deployment.wmo_id)
                with open(event.src_path) as wf:
                    deployment.wmo_id = str(wf.readline().strip())
                deployment.save()
                app.logger.info("Updated deployment %s", path_parts[0])
            # extra_atts.json will contain metadata modifications to
            # datasets.xml which should require a reload/regeneration of that
            # file.
            elif path_parts[-1] == "extra_atts.json":
                app.logger.info("extra_atts.json detected in %s", rel_path)
                deployment.save()
            else:
                # Always save the Deployment when a new dive file is added
                # so a checksum is calculated and a new deployment.json file
                # is created
                fname, ext = os.path.splitext(path_parts[-1])
                if '.nc' in ext:
                    deployment.save()
                    app.logger.info("Updated deployment %s", path_parts[0])
                    # touch the ERDDAP flag (realtime data only)
                    if not deployment.delayed_mode:
                        deployment_name = path_parts[0].split('/')[-1]
                        self.touch_erddap(deployment_name)

    def touch_erddap(self, deployment_name):
        '''
        Creates a flag file for erddap's file monitoring thread so that it reloads
        the dataset

        '''
        full_path = os.path.join(self.flagsdir, deployment_name)
        app.logger.info("Touching ERDDAP flag file at {}".format(full_path))
        # technically could async this as it's I/O, but touching a file is pretty
        # unlikely to be a bottleneck
        with open(full_path, 'w') as f:
            pass  # Causes file creation (touch)

    def on_moved(self, event):
        if isinstance(event, FileMovedEvent):
            self.file_moved_or_created(event)

    def on_modified(self, event):
        if isinstance(event, FileModifiedEvent):
            self.file_moved_or_created(event)

    def on_created(self, event):
        if isinstance(event, DirCreatedEvent):

            if self.base not in event.src_path:
                return

            rel_path = os.path.relpath(event.src_path, self.base)

            # we only care about this path if it's under a user dir
            # user/upload/deployment-name
            path_parts = rel_path.split(os.sep)

            if len(path_parts) != 3:
                return

            app.logger.info("New deployment directory: %s", rel_path)

            with app.app_context():
                self.create_deployment_if_nonexistent(path_parts)

        elif isinstance(event, FileCreatedEvent):
            self.file_moved_or_created(event)

    def create_deployment_if_nonexistent(self, path_parts):
        deployment_name = path_parts[1]
        username = os.path.split(path_parts[0])[-1]
        deployment = db.Deployment.find_one({'name': deployment_name})
        if deployment is None:
            app.logger.info(locals())
            deployment = db.Deployment()
            user = db.User.find_one({'username': username})
            app.logger.info("User is %s", user)
            if hasattr(user, '_id'):
                deployment.user_id = user._id
                deployment.name = deployment_name
                deployment.deployment_dir = os.path.join(username, deployment_name)
                deployment.glider_name = deployment_name.split("-", 1)[0]
                deployment.updated = datetime.utcnow()
                deployment.save()
                deployment.delayed_mode = deployment_name.endswith("-delayed")
                # TODO: Hook into main GDAC codebase for model creation
                # TODO: Add deployment date field
                app.logger.info(f"Created previously nonexistent deployment {deployment_name} for user {username}")

    def on_deleted(self, event):
        if isinstance(event, DirDeletedEvent):
            if self.base not in event.src_path:
                return

            rel_path = os.path.relpath(event.src_path, self.base)

            # we only care about this path if it's under a user dir
            # user/upload/deployment-name
            path_parts = rel_path.split(os.sep)

            if len(path_parts) != 3:
                return

            app.logger.info("Removed deployment directory: %s", rel_path)

            with app.app_context():
                deployment = db.Deployment.find_one({'deployment_dir': event.src_path})
                if deployment:
                    deployment.delete()


def main(handler):
    observer = Observer()
    observer.schedule(handler, path=handler.base, recursive=True)
    observer.start()

    app.logger.info("Watching user directories in %s", handler.base)

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()

    observer.join()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        'basedir',
        default=os.environ.get('DATA_ROOT', '.'),
        nargs='?'
    )
    parser.add_argument(
        'flagsdir',
        default=os.environ.get('FLAGS_DIR', '.'),
        nargs='?'
    )
    args = parser.parse_args()

    base = os.path.realpath(args.basedir)
    flagsdir = os.path.realpath(args.flagsdir)
    main(HandleDeploymentDB(base, flagsdir))
