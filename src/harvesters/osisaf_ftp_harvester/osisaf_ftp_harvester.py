import os
import re
import sys
import gzip
import yaml
import shutil
import hashlib
import requests
import numpy as np
from ftplib import FTP
from pathlib import Path
from dateutil import parser
from datetime import datetime
from xml.etree.ElementTree import parse
from urllib.request import urlopen, urlcleanup, urlretrieve


# Creates checksum from filename
def md5(fname):
    hash_md5 = hashlib.md5()
    with open(fname, 'rb') as f:
        for chunk in iter(lambda: f.read(4096), b""):
            hash_md5.update(chunk)
    return hash_md5.hexdigest()


# Extracts date from file name following regex
def getdate(regex, fname):
    ex = re.compile(regex)
    match = re.search(ex, fname)
    date = match.group()
    return date


# Queries Solr based on config information and filter query
# Returns list of Solr entries (docs)
def solr_query(config, solr_host, fq):
    solr_collection_name = config['solr_collection_name']

    getVars = {'q': '*:*',
               'fq': fq,
               'rows': 300000}

    url = f'{solr_host}{solr_collection_name}/select?'
    response = requests.get(url, params=getVars)
    return response.json()['response']['docs']


# Posts update to Solr with provided update body
# Optional return of posting status code
def solr_update(config, solr_host, update_body, r=False):
    solr_collection_name = config['solr_collection_name']

    url = f'{solr_host}{solr_collection_name}/update?commit=true'

    if r:
        return requests.post(url, json=update_body)
    else:
        requests.post(url, json=update_body)


# Pulls data files for given ftp source and date range
# If not on_aws, saves locally, else saves to s3 bucket
# Creates Solr entries for dataset, harvested granule, fields, and descendants
def osisaf_ftp_harvester(config_path='', output_path='', s3=None, on_aws=False):
    # =====================================================
    # Read configurations from YAML file
    # =====================================================
    if not config_path:
        print('No path for configuration file. Can not run harvester.')
        return

    with open(config_path, "r") as stream:
        config = yaml.load(stream, yaml.Loader)

    # =====================================================
    # Setup AWS Target Bucket
    # =====================================================
    if on_aws:
        target_bucket_name = config['target_bucket_name']
        target_bucket = s3.Bucket(target_bucket_name)
        solr_host = config['solr_host_aws']
    else:
        solr_host = config['solr_host_local']

    # =====================================================
    # Initializing required values
    # =====================================================
    dataset_name = config['ds_name']
    target_dir = f'{output_path}{dataset_name}/harvested_granules/'
    folder = f'/tmp/{dataset_name}/'

    ftp = FTP(config['host'])
    ftp.login(config['user'])

    if not on_aws:
        print(f'!!downloading files to {target_dir}')
    else:
        print(
            f'!!downloading files to {folder} and uploading to {target_bucket_name}/{dataset_name}')

    # if target path doesn't exist, make them
    if not os.path.exists(folder):
        os.makedirs(folder)

    if not os.path.exists(target_dir):
        os.makedirs(target_dir)

    docs = {}
    descendants_docs = {}

    # Query for existing harvested docs
    fq = ['type_s:harvested', f'dataset_s:{dataset_name}']
    query_docs = solr_query(config, solr_host, fq)

    if len(query_docs) > 0:
        for doc in query_docs:
            docs[doc['filename_s']] = doc

    fq = ['type_s:dataset', f'dataset_s:{dataset_name}']
    query_docs = solr_query(config, solr_host, fq)

    # Query for existing descendants docs
    fq = ['type_s:descendants', f'dataset_s:{dataset_name}']
    existing_descendants_docs = solr_query(config, solr_host, fq)

    if len(existing_descendants_docs) > 0:
        for doc in existing_descendants_docs:
            if doc['hemisphere_s']:
                key = (doc['date_s'], doc['hemisphere_s'])
            else:
                key = doc['date_s']
            descendants_docs[key] = doc

    # setup metadata
    meta = []
    item = {}
    last_success_item = {}
    granule_dates = []
    chk_time = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    now = datetime.utcnow()
    updating = False
    aws_upload = False

    start_time = datetime.strptime(
        config['start'], "%Y%m%dT%H:%M:%SZ")
    end_time = datetime.strptime(config['end'], "%Y%m%dT%H:%M:%SZ")

    start_date_dashes = f'{config["start"][:4]}-{config["start"][4:6]}-{config["start"][6:8]}'
    end_date_dashes = f'{config["end"][:4]}-{config["end"][4:6]}-{config["end"][6:8]}'

    # Construct list of dates corresponding to data time scale
    dates_in_year = list(np.arange(
        start_date_dashes, end_date_dashes, dtype='datetime64[D]'))
    dates_in_year.append(end_date_dashes)
    dates_in_year = set([(f'{date}'[:4], f'{date}'[5:7])
                         for date in dates_in_year])

    dates_in_year = sorted(dates_in_year)

    for year, month in dates_in_year:

        # build source urlbase
        urlbase = f'{config["ddir"]}{year}/{month}/'
        try:
            files = []
            ftp.dir(urlbase, files.append)
            files = [e.split()[-1] for e in files]
        except:
            print(f'Error finding files at {urlbase}')

        # Iterate through hemispheres given in config
        for region in config['regions']:

            hemi = 'nh' if region == 'north' else 'sh'

            # Apply filename filter to only get requested files
            hemi_files = [
                filename for filename in files if config["filename_filter"] in filename and hemi in filename]

            if not hemi_files:
                print(
                    f'No granules found for region {region} in {year}-{month}')

            for newfile in hemi_files:
                try:
                    if '.nc' not in newfile and '.bz2' not in newfile and '.gz' not in newfile:
                        continue

                    url = f'{urlbase}{newfile}'

                    date = getdate(config['regex'], newfile)
                    date_time = datetime.strptime(date, "%Y%m%d")
                    new_date_format = f'{date[:4]}-{date[4:6]}-{date[6:]}T00:00:00Z'

                    # Ignore granules with start time less than wanted start time
                    if (start_time > date_time):
                        continue

                    if (end_time < date_time):
                        break

                    granule_dates.append(datetime.strptime(
                        new_date_format, config['date_regex']))

                    # granule metadata setup to be populated for each granule
                    item = {}
                    item['type_s'] = 'harvested'
                    item['date_s'] = new_date_format
                    item['dataset_s'] = config['ds_name']
                    item['hemisphere_s'] = hemi
                    item['source_s'] = f'ftp://{config["host"]}/{url}'

                    # descendants metadta setup to be populated for each granule
                    descendants_item = {}
                    descendants_item['type_s'] = 'descendants'

                    # Create or modify descendants entry in Solr
                    descendants_item['dataset_s'] = item['dataset_s']
                    descendants_item['date_s'] = item["date_s"]
                    descendants_item['hemisphere_s'] = hemi
                    descendants_item['source_s'] = item['source_s']

                    updating = False
                    aws_upload = False

                    # Attempt to get last modified time of file
                    try:
                        mod_time = ftp.voidcmd("MDTM "+url)[4:]
                        mod_date_time = parser.parse(mod_time)
                        mod_time = mod_date_time.strftime(
                            "%Y-%m-%dT%H:%M:%SZ")
                        item['modified_time_dt'] = mod_time
                    except:
                        print(
                            'Cannot find last modified time. Downloading granule.')
                        mod_date_time = now

                    # If granule doesn't exist or previously failed or has been updated since last harvest
                    updating = (not newfile in docs.keys()) or (not docs[newfile]['harvest_success_b']) \
                        or (datetime.strptime(docs[newfile]['download_time_dt'], "%Y-%m-%dT%H:%M:%SZ") <= mod_date_time)

                    # If updating, download file
                    if updating:
                        local_fp = f'{folder}{config["ds_name"]}_granule.nc' if on_aws else f'{target_dir}{date[:4]}/{newfile}'

                        if not os.path.exists(f'{target_dir}{date[:4]}/'):
                            os.makedirs(f'{target_dir}{date[:4]}/')

                        # If file doesn't exist locally, download it
                        if not os.path.exists(local_fp):
                            print(f'Downloading: {local_fp}')

                            # new ftp retrieval
                            with open(local_fp, 'wb') as f:
                                ftp.retrbinary('RETR '+url, f.write)

                        # If file exists, but is out of date, download it
                        elif datetime.fromtimestamp(os.path.getmtime(local_fp)) <= mod_date_time:
                            print(f'Updating: {local_fp}')

                            # new ftp retrieval
                            with open(local_fp, 'wb') as f:
                                ftp.retrbinary('RETR '+url, f.write)

                        else:
                            print(
                                f'{newfile} already downloaded and up to date')

                        # Create checksum for file
                        item['checksum_s'] = md5(local_fp)

                        output_filename = f'{dataset_name}/{newfile}' if on_aws else newfile

                        item['pre_transformation_file_path_s'] = local_fp

                        # =====================================================
                        # Push data to s3 bucket
                        # =====================================================

                        if on_aws:
                            aws_upload = True
                            print("=========uploading file to s3=========")
                            target_bucket.upload_file(
                                local_fp, output_filename)
                            item['pre_transformation_file_path_s'] = f's3://{config["target_bucket_name"]}/{output_filename}'
                            print("======uploading file to s3 DONE=======")

                        item['harvest_success_b'] = True
                        item['filename_s'] = newfile
                        item['file_size_l'] = os.path.getsize(local_fp)

                except Exception as e:
                    print(e)
                    if updating:
                        if aws_upload:
                            print("======aws upload unsuccessful=======")
                            item['message_s'] = 'aws upload unsuccessful'

                        else:
                            print(f'Download {newfile} failed.')
                            print("======file not successful=======")

                        item['harvest_success_b'] = False
                        item['filename'] = ''
                        item['pre_transformation_file_path_s'] = ''
                        item['file_size_l'] = 0

                if updating:
                    item['download_time_dt'] = chk_time

                    # Update Solr entry using id if it exists
                    if hemi:
                        key = (descendants_item['date_s'], hemi)
                    else:
                        key = descendants_item['date_s']

                    if key in descendants_docs.keys():
                        descendants_item['id'] = descendants_docs[key]['id']

                    descendants_item['harvest_success_b'] = item['harvest_success_b']
                    descendants_item['pre_transformation_file_path_s'] = item['pre_transformation_file_path_s']
                    meta.append(descendants_item)

                    # add item to metadata json
                    meta.append(item)
                    # store meta for last successful download
                    last_success_item = item

    ftp.quit()

    # post granule metadata documents for downloaded granules
    r = solr_update(config, solr_host, meta, r=True)

    if meta:
        if r.status_code == 200:
            print('granule metadata post to Solr success')
        else:
            print('granule metadata post to Solr failed')
    else:
        print('no new granules found')

    overall_start = min(granule_dates) if granule_dates else None
    overall_end = max(granule_dates) if granule_dates else None

    # Query for Solr Dataset-level Document
    fq = ['type_s:dataset', f'dataset_s:{dataset_name}']
    docs = solr_query(config, solr_host, fq)

    # If dataset entry exists on Solr
    update = (len(docs) == 1)

    # Update Solr metadata for dataset and fields
    if not update:
        # TODO: THIS SECTION BELONGS WITH DATASET DISCOVERY

        # -----------------------------------------------------
        # Create Solr dataset entry
        # -----------------------------------------------------
        ds_meta = {}
        ds_meta['type_s'] = 'dataset'
        ds_meta['dataset_s'] = dataset_name
        ds_meta['short_name_s'] = config['original_dataset_short_name']
        ds_meta['source_s'] = f'ftp://{config["host"]}/{config["ddir"]}'
        ds_meta['data_time_scale_s'] = config['data_time_scale']
        ds_meta['date_format_s'] = config['date_format']
        ds_meta['last_checked_dt'] = chk_time
        ds_meta['original_dataset_title_s'] = config['original_dataset_title']
        ds_meta['original_dataset_short_name_s'] = config['original_dataset_short_name']
        ds_meta['original_dataset_url_s'] = config['original_dataset_url']
        ds_meta['original_dataset_reference_s'] = config['original_dataset_reference']
        ds_meta['original_dataset_doi_s'] = config['original_dataset_doi']

        if overall_start != None:
            ds_meta['start_date_dt'] = overall_start.strftime(
                "%Y-%m-%dT%H:%M:%SZ")
            ds_meta['end_date_dt'] = overall_end.strftime("%Y-%m-%dT%H:%M:%SZ")
        else:
            ds_meta['status_s'] = 'error harvesting - no files found'

        # if no ds entry yet and no qualifying downloads, still create ds entry without download time
        if updating:
            ds_meta['last_download_dt'] = last_success_item['download_time_dt']
            ds_meta['status_s'] = "harvested"
        else:
            ds_meta['status_s'] = "nodata"

        body = []
        body.append(ds_meta)

        # Update Solr with dataset metadata
        r = solr_update(config, solr_host, body, r=True)

        if r.status_code == 200:
            print('Successfully created Solr dataset document')
        else:
            print('Failed to create Solr dataset document')

        # -----------------------------------------------------
        # Create Solr dataset field entries
        # -----------------------------------------------------
        body = []
        for field in config['fields']:
            field_obj = {}
            field_obj['type_s'] = 'field'
            field_obj['dataset_s'] = dataset_name
            field_obj['name_s'] = field['name']
            field_obj['long_name_s'] = field['long_name']
            field_obj['standard_name_s'] = field['standard_name']
            field_obj['units_s'] = field['units']
            body.append(field_obj)

        # Update Solr with dataset fields metadata
        r = solr_update(config, solr_host, body, r=True)

        if r.status_code == 200:
            print('Successfully created Solr field documents')
        else:
            print('Failed to create Solr field documents')

    # if dataset entry exists, update download time, converage start date, coverage end date
    else:
        # Check start and end date coverage
        doc = docs[0]
        old_start = datetime.strptime(
            doc['start_date_dt'], "%Y-%m-%dT%H:%M:%SZ") if 'start_date_dt' in doc.keys() else None
        old_end = datetime.strptime(
            doc['end_date_dt'], "%Y-%m-%dT%H:%M:%SZ") if 'end_date_dt' in doc.keys() else None
        doc_id = doc['id']

        # build update document body
        update_doc = {}
        update_doc['id'] = doc_id
        update_doc['last_checked_dt'] = {"set": chk_time}

        if meta:
            update_doc['status_s'] = {"set": "harvested"}

            if 'download_time_dt' in last_success_item.keys():
                update_doc['last_download_dt'] = {
                    "set": last_success_item['download_time_dt']}

            if old_start == None or overall_start < old_start:
                update_doc['start_date_dt'] = {
                    "set": overall_start.strftime("%Y-%m-%dT%H:%M:%SZ")}

            if old_end == None or overall_end > old_end:
                update_doc['end_date_dt'] = {
                    "set": overall_end.strftime("%Y-%m-%dT%H:%M:%SZ")}

        # Update Solr with modified dataset entry
        r = solr_update(config, solr_host, [update_doc], r=True)

        if r.status_code == 200:
            print('Successfully updated Solr dataset document')
        else:
            print('Failed to update Solr dataset document')
