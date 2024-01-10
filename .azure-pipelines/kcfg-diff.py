import argparse
import os
import glob
import sys
import re
from tabulate import tabulate

COLUMN_WIDTH_MAX = 40
MODIF_COLUMN_WIDTH_MAX = 80

RST_TEMPLATE = '''\
SONiC-linux-kernel KConfig Difference:
==========

Reference Kernel
------------
- Version: {}
- BuildID: https://dev.azure.com/mssonic/build/_build/results?buildId={}&view=results

Latest Kernel
------------
- Version: {}
- BuildID: https://dev.azure.com/mssonic/build/_build/results?buildId={}&view=results


Additions & Deletions
------------
{}

Modifications
------------
{}
'''

def read_data(file1):
    data = []
    try:
        with open(file1, 'r') as f1:
            data = f1.readlines()
    except Exception as e:
        print("ABORT: Reading failed from {}, {}".format(file1, str(e)))
        sys.exit(1)

    data = filter(lambda line: not(line.startswith('#')), data)
    ret = dict()
    for line in data:
        tokens = line.split('=')
        if len(tokens) == 2:
            key, val = tokens[0].strip(), tokens[-1].strip()
            ret[key] = val
    return ret

def write_data(fname, data):
    try:
        with open(fname, 'w') as f:
            f.write(data)
    except Exception as e:
        print("ABORT: Writing to the file {} failed {}".format(fname, str(e)))
        sys.exit(1)

def generate_diff(file1, file2):
    data_f1 = read_data(file1)
    data_f2 = read_data(file2)
    
    additions = []
    modifications = []
    deletions = []

    for key_old, val_old in data_f1.items():
        val_new = data_f2.get(key_old, None)
        if not val_new:
            deletions.append("{}={}".format(key_old, val_old))
        elif val_old != val_new:
            modifications.append("{}={}->{}".format(key_old, val_old, val_new))
        if val_new:
            del data_f2[key_old]
    
    for key, val in data_f2.items():
        additions.append("{}={}".format(key, val))
    return additions, modifications, deletions

def restrict_column_width(lis, width):
    for i in range(0, len(lis)):
        curr_width = len(lis[i])
        new_val = ''
        num_newlines = int(curr_width/width)
        for j in range(0, num_newlines+1):
            if (j+1)*width < curr_width:
                new_val += lis[i][j*width:(j+1)*width]
                new_val += "\n"
            else:
                new_val += lis[i][j*width:]
        lis[i] = new_val

def format_diff_table(additions, modifications, deletions):
    max_len = max(len(additions), len(deletions))
    additions += [''] * (max_len - len(additions))
    deletions += [''] * (max_len - len(deletions))

    restrict_column_width(additions, COLUMN_WIDTH_MAX)
    restrict_column_width(deletions, COLUMN_WIDTH_MAX)
    restrict_column_width(modifications, MODIF_COLUMN_WIDTH_MAX)

    table_data = list(zip(additions, deletions))
    headers = ["ADDITIONS", "DELETIONS"]

    add_del = tabulate(table_data, headers=headers, tablefmt="grid")
    mod = tabulate(list(zip(modifications)), headers=["MODIFICATIONS"], tablefmt="grid")
    return add_del, mod

def parse_kver(loc):
    files = glob.glob(os.path.join(loc, "config-*"))
    if len(files) > 1:
        print("WARNING: Multiple config- files present under {}, {}".format(loc, files))
    result = re.search(r"config-(.*)", os.path.basename(files[-1]))
    return result.group(1)

def create_parser():
    # Create argument parser
    parser = argparse.ArgumentParser()

    # Optional arguments
    parser.add_argument("--arch", type=str, required=True)
    parser.add_argument("--old_kcfg", type=str, required=True)
    parser.add_argument("--new_kcfg", type=str, required=True)
    parser.add_argument("--output", type=str, required=True)
    parser.add_argument("--ref_buildid", type=str, required=True)
    parser.add_argument("--buildid", type=str, required=True)
    return parser

def verify_args(args):
    if not glob.glob(os.path.join(args.old_kcfg, "config-*")):
        print("ABORT: config file missing under {}".format(args.old_kcfg))
        return False

    if not glob.glob(os.path.join(args.new_kcfg, "config-*")):
        print("ABORT: config file missing under {}".format(args.new_kcfg))
        return False

    if not os.path.exists(os.path.dirname(args.output)):
        print("ABORT: Output Folder {} doesn't exist".format(args.output))
        return False
    return True

if __name__ == "__main__":
    parser = create_parser()
    args = parser.parse_args()
    if not verify_args(args):
        sys.exit(1)

    ver_old = parse_kver(args.old_kcfg)
    ver_new = parse_kver(args.new_kcfg)
    f_old = os.path.join(args.old_kcfg, 'config-{}'.format(ver_old))
    f_new = os.path.join(args.new_kcfg, 'config-{}'.format(ver_new))

    additions, modifications, deletions = generate_diff(f_old, f_new)
    add_del, mod = format_diff_table(additions, modifications, deletions)

    diff = RST_TEMPLATE.format(ver_old, args.ref_buildid, ver_new, args.buildid, add_del, mod)
    write_data(args.output, diff)
