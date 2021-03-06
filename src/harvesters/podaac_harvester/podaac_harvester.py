import os
import sys
import gzip
import yaml
import shutil
import hashlib
import logging
import requests
import numpy as np
import xarray as xr

from pathlib import Path
from xml.etree.ElementTree import parse
from datetime import datetime, timedelta
from urllib.request import urlopen, urlcleanup, urlretrieve

log = logging.getLogger(__name__)


# Creates checksum from filename
def md5(fname):
    hash_md5 = hashlib.md5()
    with open(fname, 'rb') as f:
        for chunk in iter(lambda: f.read(4096), b""):
            hash_md5.update(chunk)
    return hash_md5.hexdigest()


# Creates metadata entry for a harvested granule and uploads to AWS if needed
def metadata_maker(config, date, link, mod_time, on_aws, target_bucket, local_fp, file_name, chk_time, descendants_docs, item_id):
    dataset_name = config['ds_name']
    harvest_success = False

    item = {}
    if item_id:
        item['id'] = item_id
    item['type_s'] = {"set": 'harvested'}
    item['date_s'] = {"set": date}
    item['dataset_s'] = {"set": dataset_name}
    item['source_s'] = {"set": link}
    item['modified_time_s'] = {"set": mod_time}
    item['download_time_dt'] = {"set": chk_time}

    # Create or modify descendants entry in Solr
    descendants_item = {}
    descendants_item['type_s'] = {"set": 'descendants'}
    descendants_item['dataset_s'] = {"set": dataset_name}
    descendants_item['date_s'] = {"set": date}
    descendants_item['source_s'] = {"set": link}

    # Update Solr entry using id if it exists
    if date in descendants_docs.keys():
        descendants_item['id'] = descendants_docs[date]['id']

    # Create checksum for file
    harvest_success = True
    item['harvest_success_b'] = {"set": harvest_success}
    item['pre_transformation_file_path_s'] = {"set": local_fp}
    item['filename_s'] = {"set": file_name}

    try:
        item['file_size_l'] = {"set": os.path.getsize(local_fp)}
        item['checksum_s'] = {"set": md5(local_fp)}
    except Exception as e:
        log.debug(e)
        print(f'Failed updating file_size and checksum for {file_name}')
        print('=======failed file_size and checksum======')

    try:
        if on_aws:
            output_filename = f'{dataset_name}/{file_name}'
            print("=========uploading file to s3=========")
            target_bucket.upload_file(local_fp, output_filename)
            item['pre_transformation_file_path_s'] = {
                "set": f's3://{config["target_bucket_name"]}/{output_filename}'}
            print("======uploading file to s3 DONE=======")

    except Exception as e:
        print(e)
        print("======aws upload unsuccessful=======")

        harvest_success = False
        item['message_s'] = {"set": 'aws upload unsuccessful'}
        item['harvest_success_b'] = {"set": harvest_success}
        item['pre_transformation_file_path_s'] = {"set": ''}
        item['filename_s'] = {"set": ''}
        item['file_size_l'] = {"set": 0}

    descendants_item['harvest_success_b'] = {"set": harvest_success}
    pre_transformation_file_path_s = item['pre_transformation_file_path_s']["set"]
    descendants_item['pre_transformation_file_path_s'] = {
        "set": pre_transformation_file_path_s}
    return (item, descendants_item)


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


# Pulls data files for given PODAAC id and date range
# If not on_aws, saves locally, else saves to s3 bucket
# Creates Solr entries for dataset, harvested granule, fields, and descendants
def podaac_harvester(config_path='', output_path='', s3=None, on_aws=False):
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
        target_bucket = None
        solr_host = config['solr_host_local']

    # =====================================================
    # Initializing required values
    # =====================================================
    dataset_name = config['ds_name']

    target_dir = f'{output_path}{dataset_name}/harvested_granules/'
    date_regex = config['date_regex']
    aggregated = config['aggregated']
    start_time = config['start']
    end_time = config['end']

    if not on_aws:
        print(f'!!downloading files to {target_dir}')
    else:
        print(
            f'!!downloading files and uploading to {target_bucket_name}/{dataset_name}')

    if config['aggregated']:
        url = f'{config["host"]}&datasetId={config["podaac_id"]}'
    else:
        url = f'{config["host"]}&datasetId={config["podaac_id"]}&endTime={end_time}&startTime={start_time}'

    namespace = {"podaac": "http://podaac.jpl.nasa.gov/opensearch/",
                 "opensearch": "http://a9.com/-/spec/opensearch/1.1/",
                 "atom": "http://www.w3.org/2005/Atom",
                 "georss": "http://www.georss.org/georss",
                 "gml": "http://www.opengis.net/gml",
                 "dc": "http://purl.org/dc/terms/",
                 "time": "http://a9.com/-/opensearch/extensions/time/1.0/"}

    next = None
    more = True

    # if target paths don't exist, make them
    if not os.path.exists(target_dir):
        os.makedirs(target_dir)

    docs = {}
    descendants_docs = {}

    # Query for existing harvested docs
    fq = ['type_s:harvested', f'dataset_s:{dataset_name}']
    harvested_docs = solr_query(config, solr_host, fq)

    if len(harvested_docs) > 0:
        for doc in harvested_docs:
            docs[doc['filename_s']] = doc

    # Query for existing descendants docs
    fq = ['type_s:descendants', f'dataset_s:{dataset_name}']
    existing_descendants_docs = solr_query(config, solr_host, fq)

    if len(existing_descendants_docs) > 0:
        for doc in existing_descendants_docs:
            descendants_docs[doc['date_s']] = doc

    # setup metadata
    meta = []
    last_success_item = {}
    start = []
    end = []
    chk_time = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    now = datetime.utcnow()
    updating = False

    # While available granules exist
    while more:
        xml = parse(urlopen(url))

        items = xml.findall('{%(atom)s}entry' % namespace)

        # Loops through available granules to download
        for elem in items:
            updating = False

            # Prepares information necessary for download and metadata
            try:
                # download link
                link = elem.find(
                    "{%(atom)s}link[@title='OPeNDAP URL']" % namespace).attrib['href']
                link = '.'.join(link.split('.')[:-1])
                newfile = link.split("/")[-1]

                if '.nc' not in newfile and '.bz2' not in newfile and '.gz' not in newfile:
                    continue

                date_start_str = elem.find("{%(time)s}start" % namespace).text
                date_end_str = elem.find("{%(time)s}end" % namespace).text

                # Ignore granules with start time less than wanted start time
                if date_start_str.replace('-', '') < start_time and not aggregated:
                    continue

                # Remove nanoseconds
                if len(date_start_str) > 19:
                    date_start_str = date_start_str[:19] + 'Z'
                if len(date_end_str) > 19:
                    date_end_str = date_end_str[:19] + 'Z'

                start_datetime = datetime.strptime(date_start_str, date_regex)
                end_datetime = datetime.strptime(date_end_str, date_regex)

                if not aggregated:
                    start.append(start_datetime)
                    end.append(end_datetime)

                # Attempt to get last modified time of file on podaac
                # Not all PODAAC datasets contain last modified time
                try:
                    mod_time = elem.find("{%(atom)s}updated" % namespace).text
                    mod_date_time = datetime.strptime(
                        mod_time, date_regex)

                except:
                    print('Cannot find last modified time.  Downloading granule.')
                    mod_time = str(now)
                    mod_date_time = now

                # If granule doesn't exist or previously failed or has been updated since last harvest
                updating = (not newfile in docs.keys()) or (not docs[newfile]['harvest_success_b']) \
                    or (datetime.strptime(docs[newfile]['download_time_dt'], "%Y-%m-%dT%H:%M:%SZ") <= mod_date_time)

                # If updating, download file
                if updating:
                    local_fp = f'{target_dir}{date_start_str[:4]}/{newfile}'

                    if not os.path.exists(f'{target_dir}{date_start_str[:4]}'):
                        os.makedirs(f'{target_dir}{date_start_str[:4]}')

                    if newfile in docs.keys():
                        item_id = docs[newfile]['id']
                    else:
                        item_id = None

                    # If file doesn't exist locally, download it
                    if not os.path.exists(local_fp):
                        print(f'Downloading: {local_fp}')

                        urlcleanup()
                        urlretrieve(link, local_fp)

                    # If file exists, but is out of date, download it
                    elif datetime.fromtimestamp(os.path.getmtime(local_fp)) <= mod_date_time:
                        print(f'Updating: {local_fp}')

                        urlcleanup()
                        urlretrieve(link, local_fp)

                    else:
                        print('File already downloaded and up to date')

                    if aggregated:
                        # Break up into granules
                        print(
                            f'Extracting individual data granules from aggregated data file')
                        ds = xr.open_dataset(local_fp)

                        ds_times = [time for time in np.datetime_as_string(
                            ds.time.values) if start_time[:9] <= time.replace('-', '')[:9] <= end_time[:9]]

                        for time in ds_times:
                            new_ds = ds.sel(time=time)
                            file_name = f'{dataset_name}_{time.replace("-","")[:8]}.nc'
                            local_fp = f'{target_dir}{time[:4]}/{file_name}'

                            if not os.path.exists(f'{target_dir}{time[:4]}'):
                                os.makedirs(
                                    f'{target_dir}{time[:4]}')

                            new_ds.to_netcdf(path=local_fp)
                            time_s = f'{time[:-10]}Z'

                            if file_name in docs.keys():
                                item_id = docs[newfile]['id']
                            else:
                                item_id = None

                            item, descendants_item = metadata_maker(config, time_s, link, time_s, on_aws, target_bucket,
                                                                    local_fp, file_name, mod_time, descendants_docs, item_id)

                            meta.append(item)
                            meta.append(descendants_item)

                            if item['harvest_success_b']:
                                last_success_item = item

                            start.append(datetime.strptime(
                                time[:-3], '%Y-%m-%dT%H:%M:%S.%f'))
                            end.append(datetime.strptime(
                                time[:-3], '%Y-%m-%dT%H:%M:%S.%f'))

                        local_fp = f'{target_dir}{date_start_str[:4]}/{newfile}'

                    else:
                        item, descendants_item = metadata_maker(config, date_start_str, link, mod_time, on_aws,
                                                                target_bucket, local_fp, newfile, chk_time, descendants_docs, item_id)
                        meta.append(descendants_item)
                        meta.append(item)

                        if item['harvest_success_b']:
                            last_success_item = item

            except Exception as e:
                print(e)
                print(f'{file_name} unsuccessful')

        # Check if more granules are available
        next = xml.find("{%(atom)s}link[@rel='next']" % namespace)
        if next is None:
            more = False
            print(f'{dataset_name} done')
        else:
            url = next.attrib['href']

    # Update Solr with downloaded granule metadata entries
    r = solr_update(config, solr_host, meta, r=True)

    if r.status_code == 200:
        print('granule metadata post to Solr success')
    else:
        print('granule metadata post to Solr failed')

    overall_start = min(start) if len(start) > 0 else None
    overall_end = max(end) if len(end) > 0 else None

    # Query for Solr Dataset-level Document
    fq = ['type_s:dataset', f'dataset_s:{dataset_name}']
    dataset_query = solr_query(config, solr_host, fq)

    # If dataset entry exists on Solr
    update = (len(dataset_query) == 1)

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
        ds_meta['source_s'] = f'{config["host"]}&datasetId={config["podaac_id"]}'
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
            if last_success_item:
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
        dataset_metadata = dataset_query[0]
        old_start = datetime.strptime(
            dataset_metadata['start_date_dt'], "%Y-%m-%dT%H:%M:%SZ") if 'start_date_dt' in dataset_metadata.keys() else None
        old_end = datetime.strptime(
            dataset_metadata['end_date_dt'], "%Y-%m-%dT%H:%M:%SZ") if 'end_date_dt' in dataset_metadata.keys() else None
        doc_id = dataset_metadata['id']

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
