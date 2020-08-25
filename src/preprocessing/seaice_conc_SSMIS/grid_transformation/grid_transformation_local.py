import os
import sys
import glob
import yaml
import requests
import itertools
import numpy as np
import xarray as xr
from collections import defaultdict
from grid_transformation import run_locally_wrapper, solr_query, solr_update


# Determines grid/field combinations that have yet to be transformed for a given granule
# Returns dictionary where key is grid and value is list of fields
def get_remaining_transformations(config, source_file_path):
    dataset_name = config['ds_name']
    solr_host = config['solr_host_local']

    # Query for grids
    fq = ['type_s:grid']
    docs = solr_query(config, solr_host, fq)
    grids = [doc['grid_name_s'] for doc in docs]

    # Query for fields
    fq = ['type_s:field', f'dataset_s:{dataset_name}']
    docs = solr_query(config, solr_host, fq)
    fields = [field_entry for field_entry in docs]

    # Cartesian product of grid/field combinations
    grid_field_combinations = list(itertools.product(grids, fields))

    # Query for existing transformations
    fq = [f'dataset_s:{dataset_name}', 'type_s:transformation',
          f'pre_transformation_file_path_s:"{source_file_path}"']
    docs = solr_query(config, solr_host, fq)

    if len(docs) > 0:

        # Dictionary where key is grid, field tuple and value is harvested granule checksum
        # For existing transformations pulled from Solr
        existing_transformations = {
            (doc['grid_name_s'], doc['field_s']): doc['origin_checksum_s'] for doc in docs}

        drop_list = []

        for (grid, field) in grid_field_combinations:
            field_name = field['name_s']

            # If transformation exists, must compare checksums and versions for updates
            if (grid, field_name) in existing_transformations:

                # Query for harvested granule checksum
                fq = [f'dataset_s:{dataset_name}', 'type_s:harvested',
                      f'pre_transformation_file_path_s:"{source_file_path}"']
                harvested_checksum = solr_query(config, solr_host, fq)[
                    0]['checksum_s']

                origin_checksum = existing_transformations[(grid, field_name)]

                # Query for existing transformation
                fq = [f'dataset_s:{dataset_name}', 'type_s:transformation',
                      f'pre_transformation_file_path_s:"{source_file_path}"']
                transformation = solr_query(config, solr_host, fq)[0]

                # Compare transformation version number and config version number
                # Compare origin checksum for transformed file
                if transformation['success_b']:
                    if transformation['transformation_version_f'] == config['version'] and origin_checksum == harvested_checksum:

                        # Add grid/field combination to drop_list
                        drop_list.append((grid, field))

        # Remove drop_list grid/field combinations from list of remaining transformations
        grid_field_combinations = [
            combo for combo in grid_field_combinations if combo not in drop_list]

    # Build dictionary of remaining transformations
    grid_field_dict = defaultdict(list)

    for grid, field in grid_field_combinations:
        grid_field_dict[grid].append(field)

    return dict(grid_field_dict)


##################################################
if __name__ == "__main__":
    system_path = os.path.dirname(os.path.abspath(sys.argv[0]))

    # Pull config information
    path_to_yaml = f'{system_path}/grid_transformation_config.yaml'
    with open(path_to_yaml, "r") as stream:
        config = yaml.load(stream)

    dataset_name = config['ds_name']
    output_dir = config['output_dir']
    solr_host = config['solr_host_local']

    if not os.path.exists(output_dir):
        os.mkdir(output_dir)

    # Get all harvested granules for this dataset
    fq = [f'dataset_s:{dataset_name}', 'type_s:harvested']
    harvested_granules = solr_query(config, solr_host, fq)

    years_updated = []

    # For each harvested granule get remaining transformations and perform transformation
    for granule in harvested_granules:
        f = granule.get('pre_transformation_file_path_s', '')

        # Skips granules that weren't harvested properly
        if f == '':
            print("ERROR - pre transformation path doesn't exist")
            continue

        # Get transformations to be completed for this file
        remaining_transformations = get_remaining_transformations(config, f)

        # Perform remaining transformations
        if remaining_transformations:
            run_locally_wrapper(
                system_path, f, remaining_transformations, output_dir)

            # Add granule year to years_updated
            year = granule['date_s'][:4]

            if year not in years_updated:
                years_updated.append(year)

        else:
            print(f'No new transformations for {granule["date_s"]}')

    # Query Solr for dataset metadata
    fq = [f'dataset_s:{dataset_name}', 'type_s:dataset']
    dataset_metadata = solr_query(config, solr_host, fq)[0]

    # Combine Solr dataset entry years_updated list with transformation years_updated
    if 'years_updated_ss' in dataset_metadata.keys():
        dataset_years_updated = dataset_metadata['years_updated_ss']

        for year in years_updated:
            if year not in dataset_years_updated:
                dataset_years_updated.append(year)
    else:
        dataset_years_updated = years_updated

    # Update Solr dataset entry years_updated list and status to transformed
    update_body = [{
        "id": dataset_metadata['id'],
        "status_s": {"set": 'transformed'},
        "years_updated_ss": {"set": dataset_years_updated}
    }]

    r = solr_update(config, solr_host, update_body, r=True)

    if r.status_code == 200:
        print('Successfully updated Solr dataset entry with transformation information')
    else:
        print('Failed to update Solr dataset entry with transformation information')