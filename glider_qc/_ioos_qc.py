from typing import Any, Union
from ioos_qc.config import Config
from ioos_qc.streams import NumpyStream
from ioos_qc.results import collect_results
# from funcinternals.txt import debug_text as dbg

import xarray as xr
import pandas as pd
import numpy as np
import netCDF4 as nc

import yaml
import os
import json

from sys import maxsize
# np.set_printoptions(threshold=maxsize)

CONF_DIR = '_ioos_qc'
COMM_NAME = '_common.yml'

def test(ds: nc.Dataset) -> None:

    common_yml = open(os.path.join(CONF_DIR, COMM_NAME))
    common_tests = yaml.load(common_yml, Loader=yaml.SafeLoader)

    ymls = [ f for f in os.listdir(CONF_DIR) if f != COMM_NAME ]
    test_names = [ n.split('.')[0] for n in ymls ]  # tests and variables use the same names

    for i in range(len(ymls)):
        data = yaml.load(open(os.path.join(CONF_DIR, ymls[i])), Loader=yaml.SafeLoader)

        if data:
            name = test_names[i]
            data[name]['qartod'].update(common_tests)

            conf = Config(data)

            print('\n' + ('-' * 150))
            print(f'Configured tests for variable/stream "{ name }" as follows:')

            print(json.dumps(data, indent=4))
            print("Runnning tests now...\n")

            stream = NumpyStream(
                inp=ds[name][:],
                time=ds['time'][:],
                lat=ds['latitude'][:],
                lon=ds['longitude'][:],
                z=ds['depth'][:],
            )

            init_res = stream.run(conf)
            res = collect_results(init_res, how='list')

            print("\nRESULTS:")
            for r in res:
                print(r, r.results)

if __name__ == '__main__':

    base = '/Users/ben/Downloads/'

    files = [
        'usf-gansett-20210908T1200_30bc_028a_38bb.nc',
        'maracoos_02-20210820T1546_3b85_1631_d6c5.nc'
    ]

    for f in files:
        hdr = f"**** Using <{f}> ****"

        print()
        print('*' * len(hdr))

        print(hdr)
        print('*' * len(hdr))

        p = base + f
        ds = nc.Dataset(p)

        test(ds)



##EOF