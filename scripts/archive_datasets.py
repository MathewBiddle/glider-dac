#!/usr/bin/env python
'''
Script to create hard links of archivable datasets and generate an MD5 sum
'''
from config import API_URL, NCEI_DIR, path2pub
import requests
import argparse
import sys
import os
import hashlib
import logging
import shutil
import xattr

logger = logging.getLogger('archive_datasets')
_DEP_CACHE = None
DNA_SUFFIX = '.DO-NOT-ARCHIVE'


def get_deployments():
    '''
    Returns an HTTP GET request for the API_URL
    '''
    # Why is this using the API rather than hitting the database directly?
    global _DEP_CACHE
    if _DEP_CACHE is None:
        r = requests.get(API_URL)
        if r.status_code != 200:
            raise IOError("Failed to get deployments from API")
        _DEP_CACHE = r.json()
    return _DEP_CACHE


def get_active_deployments():
    '''
    Returns a list of deployments that are safe for archival.  Datasets for
    archival must meet the following criteria:

    - The dataset is completed
    - The dataset is marked for archival by NCEI
    '''
    deployments = get_deployments()
    return (d for d in deployments['results']
            if d['completed'] and d.get("archive_safe"))


def get_active_deployment_paths():
    '''
    Yields a filepath for each deployment marked completed by the API
    '''
    for d in get_active_deployments():
        filedir = os.path.join(path2pub, d['deployment_dir'])
        filename = os.path.basename(d['deployment_dir']) + '.ncCF.nc3.nc'
        filepath = os.path.join(filedir, filename)
        yield filepath


def make_copy(filepath):
    '''
    Creates a copy via hard link of the file specified in the new NCEI_DIR

    :param str filepath: Path to the source of the hard link
    '''
    filename = os.path.basename(filepath)
    target = os.path.join(NCEI_DIR, filename)
    do_not_archive_filename = target + DNA_SUFFIX
    source = filepath
    if not os.path.exists(target):
        logger.info("Creating archive dataset")
        try:
            os.link(source, target)
        except (IOError, OSError):
            logger.exception("Could not hard link to file {}".format(source))
            return
    try:
        md5sum_xattr = xattr.getxattr(filepath, "user.md5sum")
    # IOError here indicates that the xattr for the md5sum hasn't been written
    # yet, so start processing the hash
    except IOError:
        generate_hash(target)
    else:
        with open("{}.md5".format(filepath)) as f:
            md5sum_file_contents = f.read()
        # Does the md5sum in the dedicated file match the xattr value?
        # If yes, we already have this file.
        # If no, we need to process this file as the contents have likely
        # changed.
        if md5sum_file_contents != md5sum_xattr:
            generate_hash(target)

    if os.path.exists(do_not_archive_filename):
        try:
            logger.info("Removing DO NOT ARCHIVE File")
            os.unlink(do_not_archive_filename)
        except OSError:
            logger.exception("Could not remove {}".format(do_not_archive_filename))


def generate_hash(filepath):
    '''
    Creates an MD5 sum file containing the hash of the file

    :param str filepath: Path to the file to be hashed
    '''
    hasher = hashlib.md5()
    hashfile(filepath, hasher)
    md5sum = filepath + '.md5'
    hash_value = hasher.hexdigest()
    with open(md5sum, 'w') as f:
        f.write(hash_value)
        xattr.setxattr(f, "user.md5sum", hash_value)
    logger.info("Hash generated")



def hashfile(filepath, hasher, blocksize=65536):
    '''
    Uses a memory efficient scheme to hash a file

    :param str filepath: Path to the file to be hashed
    :param hashlib.algorithm hasher: The hasher to use
    :param int blocksize: Size in bytes of the memory segment
    '''
    with open(filepath, 'r') as f:
        buf = f.read(blocksize)
        while len(buf) > 0:
            hasher.update(buf)
            buf = f.read(blocksize)
    return hasher


def set_verbose():
    '''
    Enables console logging
    '''
    ch = logging.StreamHandler()
    ch.setLevel(logging.DEBUG)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    ch.setFormatter(formatter)
    logger.addHandler(ch)
    logger.setLevel(logging.DEBUG)


def remove_archive(deployment):
    '''
    Removes a deployment from the archive

    :param str deployment: Deployment name
    '''
    filename = '{}.ncCF.nc3.nc'.format(deployment)
    path = os.path.join(NCEI_DIR, filename)
    logger.info("Removing archive: %s", deployment)
    if os.path.exists(path + '.md5'):
        os.unlink(path + '.md5')
    if os.path.exists(path):
        os.unlink(path)


def mark_do_not_archive(deployment):
    '''
    Creates an empty file in the archive directory indicating the deployment
    should not be archived by NCEI. NCEI will then remove the file when their
    automations discover the empty file.

    :param str deployment: Deployment name
    '''
    filename = '{}.ncCF.nc3.nc'.format(deployment)
    path = os.path.join(NCEI_DIR, filename)
    logger.info("Marking deployment %s as DO NOT ARCHIVE", deployment)
    updated_filename = path + DNA_SUFFIX
    if os.path.exists(path):
        touch_file(updated_filename)


def touch_file(filepath):
    '''
    Creates an empty file
    '''
    if not os.path.exists(filepath):
        logger.info("Touching file %s", filepath)
        with open(filepath, 'w'):
            pass
    else:
        logger.info("File %s already exists", filepath)


def main(args):
    '''
    Script to create hard links of archivable datasets and generate an MD5 sum
    '''
    if args.verbose:
        set_verbose()
    for filepath in get_active_deployment_paths():
        logger.info("Archiving %s", filepath)
        try:
            make_copy(filepath)
        except:
            logger.exception("Failed processing for file path {}".format(filepath))

    active_deployments = [d['name'] for d in get_active_deployments()]
    for filename in os.listdir(NCEI_DIR):
        if filename.endswith('.ncCF.nc3.nc'):
            deployment = filename.split('.')[0]
            if deployment not in active_deployments:
                mark_do_not_archive(deployment)
                remove_archive(deployment)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=main.__doc__)
    parser.add_argument('-v', '--verbose', action='store_true', help='Turn on verbose output')
    args = parser.parse_args()
    sys.exit(main(args))

