import os
import sys
import yaml
import logging
import importlib
import numpy as np
import tkinter as tk
from pathlib import Path
from shutil import copyfile
from tkinter import filedialog
from collections import defaultdict


# Hardcoded output directory path for pipeline files
# Leave blank to be prompted for an output directory
output_dir = ''


def print_log(log_path):
    print('\n=========================================================')
    print(
        '===================== \033[36mPrinting log\033[0m ======================')
    print('=========================================================')

    log_dict = []
    with open(log_path) as f:
        logs = f.read().splitlines()
    for l in logs:
        log_dict.append(eval(l))

    dataset_statuses = defaultdict(lambda: defaultdict(list))

    # Must add info level items first
    for d in log_dict:
        ds = d['name'].replace('pipeline.', '').replace('.harvester', '').replace(
            '.transformation', '').replace('.aggregation', '')
        preprocessing_step = d['name'].replace(
            'pipeline.', '').replace(f'{ds}.', '')
        if len(ds) > 0:
            if d['level'] == 'INFO':
                dataset_statuses[ds][preprocessing_step].append(('INFO',
                                                                 d["message"]))
    # Then add errors
    for d in log_dict:
        ds = d['name'].replace('pipeline.', '').replace('.harvester', '').replace(
            '.transformation', '').replace('.aggregation', '')
        preprocessing_step = d['name'].replace(
            'pipeline.', '').replace(f'{ds}.', '')
        if len(ds) > 0:
            if d['level'] == 'ERROR':
                if ('ERROR', d["message"]) not in dataset_statuses[ds][preprocessing_step]:
                    dataset_statuses[ds][preprocessing_step].append(
                        ('ERROR', d["message"]))

    for ds, steps in dataset_statuses.items():
        print(f'\033[93mPipeline status for {ds}\033[0m:')
        for step, messages in steps.items():
            for (level, message) in messages:
                if level == 'INFO':
                    if 'successful' in message:
                        print(f'\t\033[92m{message}\033[0m')
                    else:
                        print(f'\t\033[91m{message}\033[0m')
                elif level == 'ERROR':
                    print(f'\t\t\033[91m{message}\033[0m')


def run_harvester(datasets, path_to_harvesters, output_dir):
    print('\n=========================================================')
    print(
        '================== \033[36mRunning harvesters\033[0m ===================')
    print('=========================================================\n')
    for ds in datasets:
        harv_logger = logging.getLogger(f'pipeline.{ds}.harvester')
        try:
            print(f'\033[93mRunning harvester for {ds}\033[0m')
            print('=========================================================')

            config_path = Path(
                f'{Path(__file__).resolve().parents[2]}/datasets/{ds}/harvester_config.yaml'
            )

            with open(config_path, 'r') as stream:
                config = yaml.load(stream, yaml.Loader)

            if 'harvester_type' in config.keys():
                harvester_type = config['harvester_type']

                if harvester_type not in ['podaac', 'osisaf_ftp', 'nsidc_ftp']:
                    print(f'{harvester_type} is not a supported harvester type.')
                    break

                path_to_code = Path(
                    f'{path_to_harvesters}/{harvester_type}_harvester/')

                if 'RDEFT4' in ds:
                    path_to_code = Path(
                        f'{path_to_harvesters}/{harvester_type}_harvester/RDEFT4_ftp_harvester/'
                    )
                    harvester = 'seaice_harvester_local'
                elif harvester_type == 'podaac':
                    harvester = 'podaac_harvester_local'
                elif harvester_type == 'osisaf_ftp':
                    harvester = 'osisaf_ftp_harvester_local'
                elif harvester_type == 'nsidc_ftp':
                    harvester = 'nsidc_ftp_harvester_local'

                sys.path.insert(1, str(path_to_code))

                try:
                    ret_import = importlib.reload(ret_import)
                except:
                    ret_import = importlib.import_module(harvester)

                ret_import.main(config_path=config_path,
                                output_path=output_dir)
                sys.path.remove(str(path_to_code))

            harv_logger.info(f'Harvest successful')
            print('\033[92mHarvest successful\033[0m')
        except Exception as e:
            sys.path.remove(str(path_to_code))
            harv_logger.info(f'Harvest failed: {e}')
            print('\033[91mHarvesting failed\033[0m')
        print('=========================================================')


def run_transformation(datasets, path_to_preprocessing, output_dir):
    print('\n=========================================================')
    print(
        '=============== \033[36mRunning transformations\033[0m =================')
    print('=========================================================\n')
    for ds in datasets:
        trans_logger = logging.getLogger(f'pipeline.{ds}.transformation')
        try:
            print(f'\033[93mRunning transformation for {ds}\033[0m')
            print('=========================================================')

            config_path = Path(
                f'{Path(__file__).resolve().parents[2]}/datasets/{ds}/transformation_config.yaml'
            )

            path_to_code = Path(
                f'{path_to_preprocessing}/grid_transformation/')

            transformer = 'grid_transformation_local'

            sys.path.insert(1, str(path_to_code))

            try:
                ret_import = importlib.reload(ret_import)
            except:
                ret_import = importlib.import_module(transformer)

            ret_import.main(config_path=config_path, output_path=output_dir)
            sys.path.remove(str(path_to_code))

            trans_logger.info(f'Transformation successful')
            print('\033[92mTransformation successful\033[0m')
        except Exception as e:
            sys.path.remove(str(path_to_code))
            trans_logger.error(f'Transform failed: {e}')
            print('\033[91mTransformation failed\033[0m')
        print('=========================================================')


def run_aggregation(datasets, path_to_preprocessing, output_dir):
    print('\n=========================================================')
    print(
        '================ \033[36mRunning aggregations\033[0m ===================')
    print('=========================================================\n')
    for ds in datasets:
        agg_logger = logging.getLogger(f'pipeline.{ds}.aggregation')
        try:
            print(f'\033[93mRunning aggregation for {ds}\033[0m')
            print('=========================================================')
            config_path = Path(
                f'{Path(__file__).resolve().parents[2]}/datasets/{ds}/aggregation_config.yaml'
            )

            path_to_code = Path(
                f'{path_to_preprocessing}/aggregation_by_year/')

            aggregation = 'aggregation_local'

            sys.path.insert(1, str(path_to_code))

            try:
                ret_import = importlib.reload(ret_import)
            except:
                ret_import = importlib.import_module(aggregation)

            ret_import.main(config_path=config_path, output_path=output_dir)
            sys.path.remove(str(path_to_code))
            agg_logger.info(f'Aggregation successful')
            print('\033[92mAggregation successful\033[0m')
        except Exception as e:
            sys.path.remove(str(path_to_code))
            agg_logger.info(f'Aggregation failed: {e}')
            print('\033[91mAggregation failed\033[0m')
        print('=========================================================')


if __name__ == '__main__':
    # path to harvester and preprocessing folders
    path_to_harvesters = Path(
        f'{Path(__file__).resolve().parents[1]}/harvesters')
    path_to_preprocessing = Path(
        f'{Path(__file__).resolve().parents[1]}/preprocessing')
    path_to_grids = Path(
        f'{Path(__file__).resolve().parents[2]}/grids_to_solr')
    path_to_datasets = Path(f'{Path(__file__).resolve().parents[2]}/datasets')

    # ------------------- Grids to Solr -------------------
    while True:
        run_grids = input(
            '\nUpdate Solr with local grid files? (Y/N): ').upper()
        if run_grids not in ['Y', 'N']:
            print(
                f'Invalid response, "{run_grids}", please enter a valid response')
        elif run_grids == 'N':
            break
        else:
            try:
                print(f'\n\033[93mRunning grids_to_solr\033[0m')
                print('=========================================================')
                grids_to_solr = 'grids_to_solr'
                sys.path.insert(1, str(path_to_grids))
                try:
                    ret_import = importlib.reload(
                        ret_import                  # pylint: disable=used-before-assignment
                    )
                except:
                    ret_import = importlib.import_module(grids_to_solr)
                ret_import.main(path=str(path_to_grids))
                sys.path.remove(str(path_to_grids))
                print('\033[92mgrids_to_solr successful\033[0m')
            except Exception as e:
                print(e)
                sys.path.remove(str(path_to_grids))
                print('\033[91mgrids_to_solr failed\033[0m')
            print('=========================================================')
            break

    # ------------------- Output directory -------------------
    while True:
        dir_response = input(
            '\nUse hardcoded output directory? (Y/N): ').upper()
        if dir_response not in ['Y', 'N']:
            print(
                f'Invalid response, "{dir_response}", please enter a valid response')
        else:
            if not output_dir or dir_response == 'N':
                print('\nPlease choose your output directory')

                root = tk.Tk()
                root.attributes('-topmost', True)
                root.withdraw()
                output_dir = f'{filedialog.askdirectory()}/'

                if output_dir == '/':
                    print('No output directory given. Exiting.')
                    sys.exit()
            else:
                output_dir = str(Path(output_dir))
                if not os.path.exists(output_dir):
                    print(f'{output_dir} is an invalid output directory. Exiting.')
                    sys.exit()
            break
    print(f'Output direcory selected as {output_dir}')

    # ------------------- Add new dataset -------------------
    while True:
        add_dataset = input('\nAdd new dataset? (Y/N): ').upper()
        if add_dataset not in ['Y', 'N']:
            print(
                f'Invalid response, "{add_dataset}", please enter a valid response')
        elif add_dataset == 'N':
            break
        else:
            try:
                path_to_tools = Path(__file__).resolve().parents[0]
                sys.path.insert(1, str(path_to_tools))
                try:
                    ret_import = importlib.reload(
                        ret_import                      # pylint: disable=used-before-assignment
                    )
                except:
                    ret_import = importlib.import_module('create_directories')
                ret_import.main()
                sys.path.remove(str(path_to_tools))
                print('\033[92mcreate_directories successful\033[0m')
            except Exception as e:
                sys.path.remove(str(path_to_tools))
                print('\033[91mcreate_directories failed\033[0m')
            print('=========================================================')
            break

    datasets = os.listdir(path_to_datasets)
    datasets = [ds for ds in datasets if ds != '.DS_Store']

    # ------------------- Run pipeline -------------------
    while True:
        print('\n------------- OPTIONS -------------')
        print('1) Run all')
        print('2) Harvesters only')
        print('3) Up to aggregation')
        print('4) Dataset input')
        print('5) Y/N for datasets')
        chosen_option = input('Enter option number: ')

        if chosen_option in ['1', '2', '3', '4', '5']:
            break
        else:
            print(
                f'Unknown option entered, "{chosen_option}", please enter a valid option\n'
            )

    # Initialize logger
    logger_path = f'{output_dir}/pipeline.log'
    logger = logging.getLogger('pipeline')
    logger.setLevel(logging.DEBUG)

    # Setup file handler to output log file
    fh = logging.FileHandler(logger_path, 'w+')
    fh.setLevel(logging.DEBUG)
    fh_formatter = logging.Formatter(
        "{'name': '%(name)s', 'level': '%(levelname)s', 'message': '%(message)s'}")
    fh.setFormatter(fh_formatter)
    logger.addHandler(fh)

    # Setup console handler to print to console
    ch = logging.StreamHandler()
    ch.setLevel(logging.ERROR)
    ch_formatter = logging.Formatter('%(message)s')
    ch.setFormatter(ch_formatter)
    logger.addHandler(ch)

    if chosen_option == '1':
        for ds in datasets:
            run_harvester([ds], path_to_harvesters, output_dir)
            run_transformation([ds], path_to_preprocessing, output_dir)
            run_aggregation([ds], path_to_preprocessing, output_dir)
    elif chosen_option == '2':
        run_harvester(datasets, path_to_harvesters, output_dir)
    elif chosen_option == '3':
        for ds in datasets:
            run_harvester([ds], path_to_harvesters, output_dir)
            run_transformation([ds], path_to_preprocessing, output_dir)
    elif chosen_option == '4':
        while True:
            wanted_ds = input('\nEnter wanted dataset: ')
            if wanted_ds not in datasets:
                print(
                    f'Invalid dataset, "{wanted_ds}", please enter a valid dataset')
            else:
                break
        while True:
            valid_steps = True
            wanted_steps = input(
                '\nEnter wanted pipeline steps: ').lower().split()
            for step in wanted_steps:
                if step not in ['harvest', 'transform', 'aggregate', 'all']:
                    print(
                        f'Invalid step, "{step}", please enter a valid pipeline step')
                    valid_steps = False
            if valid_steps:
                break
        for step in wanted_steps:
            if step == 'harvest':
                run_harvester([wanted_ds], path_to_harvesters, output_dir)
            elif step == 'transform':
                run_transformation(
                    [wanted_ds], path_to_preprocessing, output_dir)
            elif step == 'aggregate':
                run_aggregation([wanted_ds], path_to_preprocessing, output_dir)
            elif step == 'all':
                run_harvester([wanted_ds], path_to_harvesters, output_dir)
                run_transformation(
                    [wanted_ds], path_to_preprocessing, output_dir)
                run_aggregation([wanted_ds], path_to_preprocessing, output_dir)
    elif chosen_option == '5':
        for ds in datasets:
            while True:
                yes_no = input(f'\nRun pipeline for {ds}? (Y/N): ').upper()
                if (yes_no != 'E') and (yes_no != 'N') and (yes_no != 'Y'):
                    print(
                        f'Unknown option entered, "{yes_no}", please enter a valid option'
                    )
                else:
                    break
            if yes_no == 'Y':
                run_harvester([ds], path_to_harvesters, output_dir)
                run_transformation([ds], path_to_preprocessing, output_dir)
                run_aggregation([ds], path_to_preprocessing, output_dir)
            elif yes_no == 'E':
                break
            else:  # yes_no == 'N'
                continue
    print_log(logger_path)
