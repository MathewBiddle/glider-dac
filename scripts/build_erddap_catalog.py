#!/usr/bin/env python
"""
scripts/build_erddap_catalog.py

This script generates dataset "chunks" for gliderDAC deployments and
concatenates them into a single datasets.xml file for ERDDAP. This
script is run on a schedule to help keep the gliderDAC datasets in
sync with newly registered (or deleted) deployments

Details:
Only generates a new dataset chunk if the dataset has been updated since
the last time the script ran. The chunk is saved as dataset.xml in the
deployment directory.

Use the -f CLI argument to create a dataset.xml chunk for ALL the datasets

Optionally add/update metadata to a dataset by supplying a json file named extra_atts.json
to the deployment directory.

An example of extra_atts.json file which modifies the history global
attribute and the depth variable's units attribute is below

{
    "_global_attrs": {
        "history": "updated units"
    },
    "depth": {
        "units": "m"
    }
}
"""

import argparse
import fileinput
import glob
import json
import logging
import numpy as np
import os
import redis
import sys
from collections import defaultdict
from datetime import datetime, timezone
from glider_dac import app, db
from jinja2 import Template
from lxml import etree
from netCDF4 import Dataset
from pathlib import Path
import requests
from scripts.sync_erddap_datasets import sync_deployment
from glider_dac.common import log_formatter


logger = logging.getLogger(__name__)
ch = logging.StreamHandler()
ch.setLevel(logging.INFO)
ch.setFormatter(log_formatter)
logger.addHandler(ch)
logger.setLevel(logging.INFO)


erddap_mapping_dict = defaultdict(lambda: 'String',
                                  { np.int8: 'byte',
                                    np.int16: 'short',
                                    np.float32: 'float',
                                    np.float64: 'double' })

# The directory where the XML templates exist
template_dir = Path(__file__).parent.parent / "glider_dac" / "erddap" / "templates"

# Connect to redis to keep track of the last time this script ran
redis_key = 'build_erddap_catalog_last_run_deployment'
redis_host = app.config.get('REDIS_HOST', 'redis')
redis_port = app.config.get('REDIS_PORT', 6379)
redis_db = app.config.get('REDIS_DB', 0)
_redis = redis.Redis(
    host=redis_host,
    port=redis_port,
    db=redis_db
)

def inactive_datasets(deployments_set):
    try:
        resp = requests.get("http://{}/erddap/tabledap/allDatasets.csv?datasetID".format(app.config["PRIVATE_ERDDAP"]),
                            timeout=10)
        resp.raise_for_status()
        # contents of erddap datasets
        erddap_contents_set = set(resp.text.splitlines()[3:])
    except:
        logger.exception("Exception occurred while attempting to detect orphaned ERDDAP datasets")
        erddap_contents_set = set()

    return erddap_contents_set - deployments_set

def build_datasets_xml(data_root, catalog_root, force):
    """
    Cats together the head, all fragments, and foot of a datasets.xml
    """
    head_path = os.path.join(template_dir, 'datasets.head.xml')
    tail_path = os.path.join(template_dir, 'datasets.tail.xml')

    # First update the chunks of datasets.xml that need updating
    # TODO: Can we use glider_dac_watchdog to trigger the chunk creation?
    deployment_names = ((dep["name"], dep["deployment_dir"]) for dep in
                         db.Deployment.find({}, {'name': True,
                                                 'deployment_dir': True}))
    for deployment_name, deployment_dir in deployment_names:
        dataset_chunk_path = os.path.join(data_root, deployment_dir, 'dataset.xml')
        # base query to which we may add filtering to see if previously run
        query = ({"name": deployment_name})

        # from De Morgan's Laws - not cond1 or not cond2 = not (cond1 and cond2)
        # we want to run the "caching" logic only if force flag isn't set and
        # we are aren't missing the dataset.xml snippet file for this deployment
        if not (force and os.path.exists(dataset_chunk_path)):
            # Get datasets that have been updated since the last time this script ran
            try:
                last_run_ts = _redis.hget(redis_key, deployment_name) or 0
                last_run = datetime.utcfromtimestamp(int(last_run_ts))
            except Exception:
                logger.error("Error: Parsing last run for {}. ".format(deployment.name),
                             "Processing dataset anyway.")
            else:
                # there is a chance that the updated field won't be set if
                # model.save() has no files
                query['updated'] = {'$gte': last_run}
        deployment = db.Deployment.find_one(query)
        # if we couldn't find the deployment, usually due to update time not
        # being recent, skip this deployment
        if not deployment:
            continue


        try:
            chunk_contents = build_erddap_catalog_chunk(data_root, deployment)
        except Exception:
            logger.exception("Error: creating dataset chunk for {}".format(deployment_dir))
        # only attempt to write file if we were able to generate an XML snippet
        # successfully
        else:
            try:
                with open(dataset_chunk_path, 'w') as f:
                    f.write(chunk_contents)
                    # Set the timestamp of this deployment run in redis
                dt_now = datetime.now(tz=timezone.utc)
                _redis.hset(redis_key, deployment_name, int(dt_now.timestamp()))
            except:
                logger.exception("Could not write ERDDAP dataset snippet XML file {}".format(dataset_chunk_path))


    # Now loop through all the deployments and construct datasets.xml
    # store in temporary file first
    ds_tmp_path = os.path.join(catalog_root, 'datasets.xml.tmp')
    ds_path = os.path.join(catalog_root, 'datasets.xml')
    deployments_name_set = set()
    deployments = db.Deployment.find()  # All deployments now
    try:
        with open(ds_tmp_path, 'w') as f:
            for line in fileinput.input([head_path]):
                f.write(line)
            # for each deployment, get the dataset chunk
            for deployment in deployments:
                deployments_name_set.add(deployment.name)
                # First check that a chunk exists
                dataset_chunk_path = os.path.join(data_root, deployment.deployment_dir, 'dataset.xml')
                if os.path.isfile(dataset_chunk_path):
                    for line in fileinput.input([dataset_chunk_path]):
                        f.write(line)

            inactive_deployment_names = inactive_datasets(deployments_name_set)

            for inactive_deployment in inactive_deployment_names:
                f.write('\n<dataset type="EDDTableFromNcFiles" datasetID="{}" active="false"></dataset>'.format(
                            inactive_deployment))

            for line in fileinput.input([tail_path]):
                f.write(line)
        # now try moving the file to update datasets.xml
        os.rename(ds_tmp_path, ds_path)
    except OSError:
        logger.exception("Could not write to datasets.xml")

    logger.info("Wrote {} from {} deployments".format(ds_path, deployments.count()))
    # issue flag refresh to remove inactive deployments after datasets.xml written
    for inactive_deployment_name in inactive_deployment_names:
        sync_deployment(inactive_deployment_name)


def variable_sort_function(element):
    """
    Sorts by ERDDAP variable destinationName, or by
    sourceName if the former is not available.
    """
    elem_list = (element.xpath("destinationName/text()") or
                 element.xpath("sourceName/text()"))
    # sort case insensitive
    try:
        return elem_list[0].lower()
    # If there's no source or destination name, or type is not a string,
    # assume a blank string.
    # This is probably not valid in datasets.xml, but we have to do something.
    except (IndexError, AttributeError):
        return ""

def build_erddap_catalog_chunk(data_root, deployment):
    """
    Builds an ERDDAP dataset xml chunk.

    :param str data_root: The root directory where netCDF files are read from
    :param mongo.Deployment deployment: Mongo deployment model
    """
    deployment_dir = deployment.deployment_dir
    logger.info("Building ERDDAP catalog chunk for {}".format(deployment_dir))

    dir_path = os.path.join(data_root, deployment_dir)

    checksum = (deployment.checksum or '').strip()
    completed = deployment.completed
    delayed_mode = deployment.delayed_mode

    # look for a file named extra_atts.json that provides
    # variable and/or global attributes to add and/or modify
    # An example of extra_atts.json file is in the module docstring
    extra_atts = {"_global_attrs": {}}
    extra_atts_file = os.path.join(dir_path, "extra_atts.json")
    if os.path.isfile(extra_atts_file):
        logger.info("extra_atts.json file found in {}".format(deployment_dir))
        try:
            with open(extra_atts_file) as f:
                extra_atts = json.load(f)
        except Exception:
            logger.exception("Error loading file: {}".format(extra_atts_file))

    # Get the latest file from the DB (and double check just in case)
    latest_file = deployment.latest_file or get_latest_nc_file(dir_path)
    if latest_file is None:
        raise IOError('No nc files found in deployment {}'.format(deployment_dir))

    core_variables = etree.fromstring("""
    <test>
    <dataVariable>
        <sourceName>trajectory</sourceName>
        <destinationName>trajectory</destinationName>
        <dataType>String</dataType>
        <addAttributes>
            <att name="comment">A trajectory is one deployment of a glider.</att>
            <att name="ioos_category">Identifier</att>
            <att name="long_name">Trajectory Name</att>
        </addAttributes>
    </dataVariable>

    <dataVariable>
        <sourceName>global:wmo_id</sourceName>
        <destinationName>wmo_id</destinationName>
        <dataType>String</dataType>
        <addAttributes>
            <att name="ioos_category">Identifier</att>
            <att name="long_name">WMO ID</att>
            <att name="missing_value" type="string">none specified</att>
        </addAttributes>
    </dataVariable>

    <dataVariable>
        <sourceName>profile_id</sourceName>
        <destinationName>profile_id</destinationName>
        <dataType>int</dataType>
        <addAttributes>
            <att name="cf_role">profile_id</att>
            <att name="ioos_category">Identifier</att>
            <att name="long_name">Profile ID</att>
        </addAttributes>
    </dataVariable>

    <dataVariable>
        <sourceName>profile_time</sourceName>
        <destinationName>time</destinationName>
        <dataType>double</dataType>
        <addAttributes>
            <att name="ioos_category">Time</att>
            <att name="long_name">Profile Time</att>
            <att name="comment">Timestamp corresponding to the mid-point of the profile.</att>
        </addAttributes>
    </dataVariable>

    <dataVariable>
        <sourceName>profile_lat</sourceName>
        <destinationName>latitude</destinationName>
        <dataType>double</dataType>
        <addAttributes>
            <att name="colorBarMaximum" type="double">90.0</att>
            <att name="colorBarMinimum" type="double">-90.0</att>
            <att name="valid_max" type="double">90.0</att>
            <att name="valid_min" type="double">-90.0</att>
            <att name="ioos_category">Location</att>
            <att name="long_name">Profile Latitude</att>
            <att name="comment">Value is interpolated to provide an estimate of the latitude at the mid-point of the profile.</att>
        </addAttributes>
    </dataVariable>

    <dataVariable>
        <sourceName>profile_lon</sourceName>
        <destinationName>longitude</destinationName>
        <dataType>double</dataType>
        <addAttributes>
            <att name="colorBarMaximum" type="double">180.0</att>
            <att name="colorBarMinimum" type="double">-180.0</att>
            <att name="valid_max" type="double">180.0</att>
            <att name="valid_min" type="double">-180.0</att>
            <att name="ioos_category">Location</att>
            <att name="long_name">Profile Longitude</att>
            <att name="comment">Value is interpolated to provide an estimate of the longitude at the mid-point of the profile.</att>
        </addAttributes>
    </dataVariable>

    <dataVariable>
        <sourceName>depth</sourceName>
        <destinationName>depth</destinationName>
        <dataType>float</dataType>
        <addAttributes>
            <att name="colorBarMaximum" type="double">2000.0</att>
            <att name="colorBarMinimum" type="double">0.0</att>
            <att name="colorBarPalette">OceanDepth</att>
            <att name="ioos_category">Location</att>
            <att name="long_name">Depth</att>
        </addAttributes>
    </dataVariable>
    </test>
    """).findall("dataVariable")

    common_variables = etree.fromstring(f"""
    <test>
    <dataVariable>
        <sourceName>pressure</sourceName>
        <destinationName>pressure</destinationName>
        <dataType>float</dataType>
        <addAttributes>
            <att name="colorBarMaximum" type="double">2000.0</att>
            <att name="colorBarMinimum" type="double">0.0</att>
            <att name="ioos_category">Pressure</att>
            <att name="long_name">Sea Water Pressure</att>
        </addAttributes>
    </dataVariable>

    <dataVariable>
        <sourceName>temperature</sourceName>
        <destinationName>temperature</destinationName>
        <dataType>float</dataType>
        <addAttributes>
            <att name="colorBarMaximum" type="double">32.0</att>
            <att name="colorBarMinimum" type="double">0.0</att>
            <att name="ioos_category">Temperature</att>
            <att name="long_name">Sea Water Temperature</att>
        </addAttributes>
    </dataVariable>

    <dataVariable>
        <sourceName>conductivity</sourceName>
        <destinationName>conductivity</destinationName>
        <dataType>float</dataType>
        <addAttributes>
            <att name="colorBarMaximum" type="double">9.0</att>
            <att name="colorBarMinimum" type="double">0.0</att>
            <att name="ioos_category">Salinity</att>
            <att name="long_name">Sea Water Electrical Conductivity</att>
        </addAttributes>
    </dataVariable>

    <dataVariable>
        <sourceName>salinity</sourceName>
        <destinationName>salinity</destinationName>
        <dataType>float</dataType>
        <addAttributes>
            <att name="colorBarMaximum" type="double">37.0</att>
            <att name="colorBarMinimum" type="double">30.0</att>
            <att name="ioos_category">Salinity</att>
            <att name="long_name">Sea Water Practical Salinity</att>
        </addAttributes>
    </dataVariable>

    <dataVariable>
        <sourceName>density</sourceName>
        <destinationName>density</destinationName>
        <dataType>float</dataType>
        <addAttributes>
            <att name="colorBarMaximum" type="double">1032.0</att>
            <att name="colorBarMinimum" type="double">1020.0</att>
            <att name="ioos_category">Other</att>
            <att name="long_name">Sea Water Density</att>
        </addAttributes>
    </dataVariable>

    <dataVariable>
        <sourceName>lat</sourceName>
        <destinationName>precise_lat</destinationName>
        <dataType>double</dataType>
        <addAttributes>
            <att name="ancillary_varibles">precise_lat_qc</att>
            <att name="colorBarMaximum" type="double">90.0</att>
            <att name="colorBarMinimum" type="double">-90.0</att>
            <att name="ioos_category">Location</att>
            <att name="long_name">Precise Latitude</att>
            <att name="comment">Interpolated latitude at each point in the time-series</att>
        </addAttributes>
    </dataVariable>

    <dataVariable>
        <sourceName>lon</sourceName>
        <destinationName>precise_lon</destinationName>
        <dataType>double</dataType>
        <addAttributes>
            <att name="ancillary_varibles">precise_lon_qc</att>
            <att name="colorBarMaximum" type="double">180.0</att>
            <att name="colorBarMinimum" type="double">-180.0</att>
            <att name="ioos_category">Location</att>
            <att name="long_name">Precise Longitude</att>
            <att name="comment">Interpolated longitude at each point in the time-series</att>
        </addAttributes>
    </dataVariable>

    <dataVariable>
        <sourceName>time</sourceName>
        <destinationName>precise_time</destinationName>
        <dataType>double</dataType>
        <addAttributes>
            <att name="ioos_category">Time</att>
            <att name="long_name">Precise Time</att>
            <att name="comment">Timestamp at each point in the time-series</att>
        </addAttributes>
    </dataVariable>

    <dataVariable>
        <sourceName>time_uv</sourceName>
        <destinationName>time_uv</destinationName>
        <dataType>double</dataType>
        <addAttributes>
            <att name="ioos_category">Time</att>
            <att name="long_name">Depth-averaged Time</att>
        </addAttributes>
    </dataVariable>

    <dataVariable>
        <sourceName>lat_uv</sourceName>
        <destinationName>lat_uv</destinationName>
        <dataType>double</dataType>
        <addAttributes>
            <att name="colorBarMaximum" type="double">90.0</att>
            <att name="colorBarMinimum" type="double">-90.0</att>
            <att name="ioos_category">Location</att>
            <att name="long_name">Depth-averaged Latitude </att>
        </addAttributes>
    </dataVariable>

    <dataVariable>
        <sourceName>lon_uv</sourceName>
        <destinationName>lon_uv</destinationName>
        <dataType>double</dataType>
        <addAttributes>
            <att name="colorBarMaximum" type="double">180.0</att>
            <att name="colorBarMinimum" type="double">-180.0</att>
            <att name="ioos_category">Location</att>
            <att name="long_name">Depth-averaged Longitude</att>
        </addAttributes>
    </dataVariable>

    <dataVariable>
        <sourceName>u</sourceName>
        <destinationName>u</destinationName>
        <dataType>double</dataType>
        <addAttributes>
            <att name="colorBarMaximum" type="double">0.5</att>
            <att name="colorBarMinimum" type="double">-0.5</att>
            <att name="coordinates">lon_uv lat_uv time_uv</att>
            <att name="ioos_category">Currents</att>
            <att name="long_name">Depth-averaged Eastward Sea Water Velocity</att>
        </addAttributes>
    </dataVariable>

    <dataVariable>
        <sourceName>v</sourceName>
        <destinationName>v</destinationName>
        <dataType>double</dataType>
        <addAttributes>
            <att name="colorBarMaximum" type="double">0.5</att>
            <att name="colorBarMinimum" type="double">-0.5</att>
            <att name="coordinates">lon_uv lat_uv time_uv</att>
            <att name="ioos_category">Currents</att>
            <att name="long_name">Depth-averaged Northward Sea Water Velocity</att>
        </addAttributes>
    </dataVariable>

    <dataVariable>
        <sourceName>platform</sourceName>
        <destinationName>platform_meta</destinationName>
        <dataType>byte</dataType>
        <addAttributes>
            <att name="ioos_category">Identifier</att>
            <att name="long_name">Platform Metadata</att>
            <att name="units">1</att>
        </addAttributes>
    </dataVariable>

    <dataVariable>
        <sourceName>instrument_ctd</sourceName>
        <destinationName>instrument_ctd</destinationName>
        <dataType>byte</dataType>
        <addAttributes>
            <att name="ioos_category">Identifier</att>
            <att name="long_name">CTD Metadata</att>
            <att name="units">1</att>
        </addAttributes>
    </dataVariable>
    </test>
    """).findall("dataVariable")


    # variables which need to have the variable {var_name}_qc present in the
    # template.  Right now these are all the same, so are hardcoded
    required_qc_vars = {"conductivity_qc", "density_qc", "depth_qc",
                        "latitude_qc", "lat_uv_qc", "longitude_qc",
                        "lon_uv_qc", "profile_lat_qc", "profile_lon_qc",
                        "pressure_qc", "salinity_qc", "temperature_qc",
                        "time_qc", "time_uv_qc", "profile_time_qc",
                        "u_qc", "v_qc"}

    # any destinationNames that need to have a different name.
    # by default the destinationName will equal the sourceName
    dest_var_remaps = {'longitude_qc': 'precise_lon_qc',
                       'latitude_qc': 'precise_lat_qc',
                       'profile_lon_qc': 'longitude_qc',
                       'profile_lat_qc': 'latitude_qc',
                       'time_qc': 'precise_time_qc',
                       'profile_time_qc': 'time_qc'}

    existing_varnames = {'trajectory', 'wmo_id', 'profile_id', 'profile_time',
                         'profile_lat', 'profile_lon', 'time', 'depth',
                         'pressure', 'temperature', 'conductivity', 'salinity',
                         'density', 'lat', 'lon', 'time_uv', 'lat_uv',
                         'lon_uv', 'u', 'v', 'platform', 'instrument_ctd'}


    nc_file = os.path.join(data_root, deployment_dir, latest_file)
    with Dataset(nc_file, 'r') as ds:
        qc_var_types = check_for_qc_vars(ds)
        #qc_vars = qc_var_snippets(required_qc_vars, qc_var_types)
        # need to explicitly cast keys to set in Python 2
        exclude_vars = (existing_varnames | set(dest_var_remaps.keys()) |
                        required_qc_vars | {'latitude', 'longitude'} |
                        qc_var_types['gen_qc'].keys() | qc_var_types['qartod'].keys())
        all_other_vars = [add_erddap_var_elem(var) for var in
                          ds.get_variables_by_attributes(name=lambda n: n not in exclude_vars)]
        gts_ingest = getattr(ds, 'gts_ingest', 'true')  # Set default value to true
        qc_vars_snippet = qc_var_snippets(required_qc_vars, qc_var_types, dest_var_remaps)

        vars_sorted = sorted(common_variables +
                             qc_vars_snippet + all_other_vars,
                             key=variable_sort_function)
        variable_order = core_variables + vars_sorted
        # Add any of the extra variables and attributes
        reload_template = "<reloadEveryNMinutes>{}</reloadEveryNMinutes>"
        if completed or delayed_mode:
            reload_settings = reload_template.format(720)
        else:
            reload_settings = reload_template.format(10)
        try:
            tree = etree.fromstring(f"""
                <dataset type="EDDTableFromNcFiles" datasetID="{deployment.name}" active="true">
                    <!-- defaultDataQuery uses datasetID -->
                    <!--
                    <defaultDataQuery>&amp;trajectory={deployment.name}</defaultDataQuery>
                    <defaultGraphQuery>longitude,latitude,time&amp;.draw=markers&amp;.marker=2|5&.color=0xFFFFFF&.colorBar=|||||</defaultGraphQuery>
                    -->
                    {reload_settings}
                    <updateEveryNMillis>-1</updateEveryNMillis>
                    <!-- use datasetID as the directory name -->
                    <fileDir>{dir_path}</fileDir>
                    <recursive>false</recursive>
                    <fileNameRegex>.*\.nc</fileNameRegex>
                    <metadataFrom>last</metadataFrom>
                    <sortedColumnSourceName>time</sortedColumnSourceName>
                    <sortFilesBySourceNames>trajectory time</sortFilesBySourceNames>
                    <fileTableInMemory>false</fileTableInMemory>
                    <accessibleViaFiles>true</accessibleViaFiles>
                    <addAttributes>
                        <att name="cdm_data_type">trajectoryProfile</att>
                        <att name="featureType">trajectoryProfile</att>
                        <att name="cdm_trajectory_variables">trajectory,wmo_id</att>
                        <att name="cdm_profile_variables">time_uv,lat_uv,lon_uv,u,v,profile_id,time,latitude,longitude</att>
                        <att name="subsetVariables">wmo_id,trajectory,profile_id,time,latitude,longitude</att>

                        <att name="Conventions">Unidata Dataset Discovery v1.0, COARDS, CF-1.6</att>
                        <att name="keywords">AUVS > Autonomous Underwater Vehicles, Oceans > Ocean Pressure > Water Pressure, Oceans > Ocean Temperature > Water Temperature, Oceans > Salinity/Density > Conductivity, Oceans > Salinity/Density > Density, Oceans > Salinity/Density > Salinity, glider, In Situ Ocean-based platforms > Seaglider, Spray, Slocum, trajectory, underwater glider, water, wmo</att>
                        <att name="keywords_vocabulary">GCMD Science Keywords</att>
                        <att name="Metadata_Conventions">Unidata Dataset Discovery v1.0, COARDS, CF-1.6</att>
                        <att name="sourceUrl">(local files)</att>
                        <att name="infoUrl">https://gliders.ioos.us/erddap/</att>
                        <!-- title=datasetID -->
                        <att name="title">{deployment.name}</att>
                        <att name="ioos_dac_checksum">{checksum}</att>
                        <att name="ioos_dac_completed">{completed}</att>
                        <att name="gts_ingest">{gts_ingest}</att>
                    </addAttributes>
                </dataset>
                """)
            for var in variable_order:
                tree.append(var)
            for identifier, mod_attrs in extra_atts.items():
                add_extra_attributes(tree, identifier, mod_attrs)
        except Exception:
            logger.exception("Exception occurred while adding atts to template: {}".format(deployment_dir))
        finally:
            return etree.tostring(tree, encoding=str)

def qc_var_snippets(required_vars, qc_var_types, dest_var_remaps):
    var_list = []
    for req_var in required_vars:
        # If the required non-QARTOD QC variable isn't already defined,
        # then supply a set of default attributes.
        if req_var not in qc_var_types['gen_qc']:
            flag_atts = """
                <att name="flag_values" type="byteList">0 1 2 3 4 5 6 7 8 9</att>
                <att name="flag_meanings">no_qc_performed good_data probably_good_data bad_data_that_are_potentially_correctable bad_data value_changed interpolated_value missing_value</att>
                <att name="_FillValue" type="byte">-127</att>
                <att name="valid_min" type="byte">0</att>
                <att name="valid_max" type="byte">9</att>
            """
        else:
            flag_atts = ""

        var_str = f"""
        <dataVariable>
            <sourceName>{req_var}</sourceName>
            <destinationName>{dest_var_remaps.get(req_var, req_var)}</destinationName>
            <dataType>byte</dataType>
            <addAttributes>
                <att name="ioos_category">Quality</att>
                <att name="long_name">{dest_var_remaps.get(req_var, req_var).replace('_qc', '')} Variable Quality Flag</att>
                {flag_atts}
            </addAttributes>
        </dataVariable>"""
        var_list.append(etree.fromstring(var_str))

    for qartod_var in qc_var_types["qartod"]:
    # if there are QARTOD variables defined, populate them

       if "_FillValue" not in qc_var_types['qartod'][qartod_var]:
           fill_value_snippet = '<att name="_FillValue" type="byte">-127</att>'
       else:
           fill_value_snippet = ""
        # all qartod variables should have _FillValue, missing_value,
        # flag_values, and flag_meanings defined

       qartod_snip = f"""
       <dataVariable>
           <sourceName>{qartod_var}</sourceName>
           <destinationName>{qartod_var}</destinationName>
           <dataType>byte</dataType>
           <addAttributes>
              <att name="ioos_category">Quality</att>
              <att name="flag_values" type="byteList">1 2 3 4 9</att>
              <att name="flag_meanings">GOOD NOT_EVALUATED SUSPECT BAD MISSING</att>
              <att name="valid_min" type="byte">1</att>
              <att name="valid_max" type="byte">9</att>
              {fill_value_snippet}
           </addAttributes>
       </dataVariable>
       """
       var_list.append(etree.fromstring(qartod_snip))

    for gen_qc_var in qc_var_types["gen_qc"]:
        # if we already have this variable as part of required QC variables,
        # skip it.
        if gen_qc_var in required_vars:
            continue
        # assume byte for data type as it is required
        gen_qc_snip = f"""
            <dataVariable>
               <sourceName>{gen_qc_var}</sourceName>
               <dataType>byte</dataType>
               <addAttributes>
                  <att name="ioos_category">Quality</att>
               </addAttributes>
            </dataVariable>
            """
        var_list.append(etree.fromstring(gen_qc_snip))

    return var_list

def add_erddap_var_elem(var):
    """
    Adds an unhandled standard name variable to the ERDDAP datasets.xml
    """
    dvar_elem = etree.Element('dataVariable')
    source_name = etree.SubElement(dvar_elem, 'sourceName')
    source_name.text = var.name
    data_type = etree.SubElement(dvar_elem, 'dataType')
    if var.dtype == str:
        data_type.text = "String"
    else:
        data_type.text = erddap_mapping_dict[var.dtype.type]
    add_atts = etree.SubElement(dvar_elem, 'addAttributes')
    ioos_category = etree.SubElement(add_atts, 'att', name='ioos_category')
    ioos_category.text = "Other"
    return dvar_elem


def add_extra_attributes(tree, identifier, mod_atts):
    """
    Adds extra user-defined attributes to the ERDDAP datasets.xml.
    Usually sourced from the extra_atts.json file, this function modifies an
    ERDDAP xml datasets tree.   `identifier` should either be "_global_attrs"
    to modify a global attribute, or the name of a variable in the dataset
    to modify a variable's attributes.  `mod_atts` is a dict with the attributes
    to create or modify.
    """
    if identifier == '_global_attrs':
        xpath_expr = "."
    else:
        xpath_expr = "dataVariable[sourceName='{}']".format(identifier)
    subtree = tree.find(xpath_expr)

    if subtree is None:
        logger.warning("Element specified By XPath expression {} not found, skipping".format(xpath_expr))
        return

    add_atts_found = subtree.find('addAttributes')
    if add_atts_found is not None:
        add_atts_elem = add_atts_found
    else:
        add_atts_elem = subtree.append(etree.Element('addAttributes'))
        logger.info('Added "addAttributes" to xpath for {}'.format(xpath_expr))

    for att_name, value in mod_atts.items():
        # find the attribute
        found_elem = add_atts_elem.find(att_name)
        # attribute exists, update current value
        if found_elem is not None:
            found_elem.text = value
        # attribute
        else:
            new_elem = etree.Element('att', {'name': att_name})
            new_elem.text = value
            add_atts_elem.append(new_elem)


def check_for_qc_vars(nc):
    """
    Checks for general gc variables and QARTOD variables by naming conventions.
    Returns a dict with both sets of variables as keys, and their attributes
    as values.
    """
    qc_vars = {'gen_qc': {}, 'qartod': {}}
    for var in nc.variables:
        if var.endswith('_qc'):
            qc_vars['gen_qc'][var] = nc.variables[var].ncattrs()
        elif var.startswith('qartod'):
            qc_vars['qartod'][var] = nc.variables[var].ncattrs()
    return qc_vars


def get_latest_nc_file(root):
    '''
    Returns the lastest netCDF file found in the directory

    :param str root: Root of the directory to scan
    '''
    list_of_files = glob.glob('{}/*.nc'.format(root))
    if not list_of_files:  # Check for no files
        return None
    return max(list_of_files, key=os.path.getctime)


def main(data_dir, catalog_dir, force):
    '''
    Entrypoint for build ERDDAP catalog script.
    '''
    # ensure datasets.xml directory exists
    os.makedirs(catalog_dir, exist_ok=True)
    build_datasets_xml(data_dir, catalog_dir, force)


if __name__ == "__main__":
    '''
    build_erddap_catalog.py priv_erddap ./data/data/priv_erddap ./data/catalog ./glider_dac/erddap/templates/private
    '''
    parser = argparse.ArgumentParser()
    parser.add_argument('data_dir', help='The directory where netCDF files are read from')
    parser.add_argument('catalog_dir', help='The full path to where the datasets.xml will reside')
    parser.add_argument('-f', '--force', action="store_true", help="Force processing ALL deployments")

    args = parser.parse_args()

    catalog_dir = os.path.realpath(args.catalog_dir)
    data_dir = os.path.realpath(args.data_dir)
    force = args.force

    with app.app_context():
        sys.exit(main(data_dir, catalog_dir, force))
