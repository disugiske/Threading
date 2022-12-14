#!/usr/bin/env python
# -*- coding: utf-8 -*-
import os
import gzip
import queue
import sys
import glob
import logging
import collections
import threading
import time
from multiprocessing import Pool
from optparse import OptionParser
# brew install protobuf
# protoc  --python_out=. ./appsinstalled.proto
# pip install protobuf
import appsinstalled_pb2
# pip install python-memcached
import memcache

NORMAL_ERR_RATE = 0.01
RETRY = 1
TIMEOUT = 1
AppsInstalled = collections.namedtuple("AppsInstalled", ["dev_type", "dev_id", "lat", "lon", "apps"])
threads = 5
q = queue.Queue()
barrier = threading.Barrier(threads)
thr = []


class PresistentConnect:
    def __init__(self, memc_addr):
        self.memc_addr = memc_addr
        self.pool = {}

    def set(self, *args, **kwargs):
        memc = self.connect()
        set_res = memc.set(*args, **kwargs)
        if not set_res:
            logging.error('Connection error')
            for i in range(RETRY):
                set_res = memc.set(*args, **kwargs)
                if set_res:
                    break
                if i == RETRY-1:
                    raise ConnectionError
                time.sleep(TIMEOUT)


    def connect(self):
        if self.memc_addr in self.pool:
            return self.pool[self.memc_addr]
        self.pool[self.memc_addr] = memcache.Client([self.memc_addr])
        return self.pool[self.memc_addr]



def dot_rename(path):
    # atomic in most cases
    if os.path.exists(path):
        head, fn = os.path.split(path)
        os.rename(path, os.path.join(head, "." + fn))



def insert_appsinstalled(memc_addr, appsinstalled, dry_run=False):
    ua = appsinstalled_pb2.UserApps()
    ua.lat = appsinstalled.lat
    ua.lon = appsinstalled.lon
    key = "%s:%s" % (appsinstalled.dev_type, appsinstalled.dev_id)
    ua.apps.extend(appsinstalled.apps)
    packed = ua.SerializeToString()
    try:
        if dry_run:
            logging.debug("%s - %s -> %s" % (memc_addr, key, str(ua).replace("\n", " ")))
        else:
            memc = PresistentConnect(memc_addr)
            memc.set(key, packed)
    except Exception as e:
        logging.exception("Cannot write to memc %s: %s" % (memc_addr, e))
        return False
    return True


def parse_appsinstalled(line):
    line_parts = line.strip().split("\t")
    if len(line_parts) < 5:
        return
    dev_type, dev_id, lat, lon, raw_apps = line_parts
    if not dev_type or not dev_id:
        return
    try:
        apps = [int(a.strip()) for a in raw_apps.split(",")]
    except ValueError:
        apps = [int(a.strip()) for a in raw_apps.split(",") if a.isidigit()]
        logging.info("Not all user apps are digits: `%s`" % line)
    try:
        lat, lon = float(lat), float(lon)
    except ValueError:
        logging.info("Invalid geo coords: `%s`" % line)
    return AppsInstalled(dev_type, dev_id, lat, lon, apps)


def worker(bar, device_memc, options):
    processed = errors = 0
    bar.wait()
    logging.info('Start %s' % threading.current_thread().name)
    while True:
        if q.empty():
            break
        line = q.get().decode().strip()
        if not line:
            q.task_done()
            continue
        appsinstalled = parse_appsinstalled(line)
        if not appsinstalled:
            errors += 1
            q.task_done()
            continue
        memc_addr = device_memc.get(appsinstalled.dev_type)
        if not memc_addr:
            errors += 1
            logging.error("Unknow device type: %s" % appsinstalled.dev_type)
            q.task_done()
            continue
        ok = insert_appsinstalled(memc_addr, appsinstalled, options.dry)
        if ok:
            processed += 1
        else:
            errors += 1
        q.task_done()
    if processed == 0:
        err_rate = 1
    else:
        err_rate = float(errors) / processed
    if err_rate < NORMAL_ERR_RATE:
        logging.info(f"Thread: {threading.current_thread().name}. "
                     f"Acceptable error rate ({err_rate}). Successfull load")
    else:
        logging.error(f"Thread: {threading.current_thread().name}. "
                      "High error rate (%s > %s). Failed load" % (err_rate, NORMAL_ERR_RATE))


def thread_queue(device_memc, options):
    for i in range(threads):
        thread = threading.Thread(target=worker, args=(barrier, device_memc, options), name=f'thr{i}')
        thr.append(thread)
        thread.start()


def load_queue(gz):
    with gzip.open(gz) as fd:
        for item in fd:
            q.put(item)
    dot_rename(gz)


def main(options):
    device_memc = {
        "idfa": options.idfa,
        "gaid": options.gaid,
        "adid": options.adid,
        "dvid": options.dvid,
    }
    for fn in glob.iglob(options.pattern):
        logging.info('Processing %s' % fn)
        load_queue(fn)
        thread_queue(device_memc, options)
        q.join()
        dot_rename(fn)


def prototest():
    sample = "idfa\t1rfw452y52g2gq4g\t55.55\t42.42\t1423,43,567,3,7,23\ngaid\t7rfw452y52g2gq4g\t55.55\t42.42\t7423,424"
    for line in sample.splitlines():
        dev_type, dev_id, lat, lon, raw_apps = line.strip().split("\t")
        apps = [int(a) for a in raw_apps.split(",") if a.isdigit()]
        lat, lon = float(lat), float(lon)
        ua = appsinstalled_pb2.UserApps()
        ua.lat = lat
        ua.lon = lon
        ua.apps.extend(apps)
        packed = ua.SerializeToString()
        unpacked = appsinstalled_pb2.UserApps()
        unpacked.ParseFromString(packed)
        assert ua == unpacked


if __name__ == '__main__':
    op = OptionParser()
    op.add_option("-t", "--test", action="store_true", default=False)
    op.add_option("-l", "--log", action="store", default=None)
    op.add_option("--dry", action="store_true", default=False)
    op.add_option("--pattern", action="store", default="*.tsv.gz")
    op.add_option("--idfa", action="store", default="127.0.0.1:33013")
    op.add_option("--gaid", action="store", default="127.0.0.1:33014")
    op.add_option("--adid", action="store", default="127.0.0.1:33015")
    op.add_option("--dvid", action="store", default="127.0.0.1:33016")
    (opts, args) = op.parse_args()
    logging.basicConfig(filename=opts.log, level=logging.INFO if not opts.dry else logging.DEBUG,
                        format='[%(asctime)s] %(levelname).1s %(message)s', datefmt='%Y.%m.%d %H:%M:%S')
    if opts.test:
        prototest()
        sys.exit(0)

    logging.info("Memc loader started with options: %s" % opts)
    try:
        start = time.time()
        main(opts)
        print(time.time() - start)
    except Exception as e:
        logging.exception("Unexpected error: %s" % e)
        sys.exit(1)
