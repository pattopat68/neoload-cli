import click
import argparse
from urllib.parse import urlparse

from neoload_cli_lib import tools, running_tools, rest_crud, displayer
from neoload_cli_lib.name_resolver import Resolver
from commands import project as cli_project, run as cli_run

import yaml
from yaml.scanner import ScannerError
import os
import copy
import tempfile
import shutil
import logging
from contextlib import redirect_stdout
import io
import sys
import json

from signal import signal, SIGINT
import time

import requests


__results_endpoint = "/test-results"
__operation_statistics = "/statistics"
__operation_sla_global = "/slas/statistics"
__operation_sla_test = "/slas/per-test"
__operation_sla_interval = "/slas/per-interval"

__results_resolver = Resolver(__results_endpoint, rest_crud.base_endpoint_with_workspace)

#neoload compose ab '-n 12 -c 3 "https://nominatim.openstreetmap.org/search?format=json&q=Boston"'

@click.command()
@click.argument('model', type=click.Choice(['ab'], case_sensitive=False))
@click.argument("meta", type=str, required=False)
@click.option("--write-to", 'writeto', default=None, help="Write YAML out to a directory")
@click.option("--overwrite", 'overwrite', is_flag=True, default=False, help="Overwrite contents if already exist; must be explicit")
@click.option("--upload-and-run-as", 'uploadandrunas', default=None, help="Use current test-settings to upload and run immediately.")
@click.pass_context
def cli(ctx, model, writeto, overwrite, uploadandrunas, meta):
    """Create a YAML-based project based on a known model for load test specifications"""
    if not model:
        tools.system_exit({'message': "model is mandatory. Please see neoload compose --help", 'code': 2})
        return

    alles = None
    ab_parsed = None

    if model == "ab": # Apache benchmark
        if meta is not None and len(meta.strip())>0:
            ab_parsed = compose_ab(meta)
            alles = ab_parsed['project']
        else:
            tools.system_exit({'message': "Model 'ab' requires a string containing all Apache Benchmark options.", 'code': 2})
            return
    else:
        tools.system_exit({'message': "Invalid command. Please see neoload compose --help", 'code': 2})
        return

    temp_dir = None
    all_in_one = False

    if uploadandrunas is not None:
        if writeto is None:
            temp_dir = tempfile.mkdtemp()
            writeto = temp_dir + "/default.yaml"
            all_in_one = True

    if writeto is not None:

        writeto = os.path.expanduser(writeto)

        if os.path.exists(writeto):
            if temp_dir is None:
                if not overwrite:
                    tools.system_exit({'message': "You specified a write-to path which exists, but no overwrite argument provided!", 'code': 2})
                    return

            if os.path.isfile(writeto):
                all_in_one = True

        if all_in_one:
            write_object_to_file(alles,writeto)
        else:
            write_project_to_dir(alles,writeto)

    if uploadandrunas:
        # do it
        nameorid = uploadandrunas
        scenario_name = "Scenario1"
        MIN_WEB_VUS = 20
        web_vu = MIN_WEB_VUS

        f = io.StringIO()
        with redirect_stdout(f):
            ctx.invoke(cli_project.cli, command="up", name_or_id=nameorid, path=writeto)
            logging.debug('Got stdout: "{0}"'.format(f.getvalue()))
            result = json.loads(f.getvalue())
            web_vu = result["scenarios"][0]["scenarioVUs"]
            scenario_name = result["scenarios"][0]["scenarioName"]

        if temp_dir is not None:
            logging.debug("Deleting temp directory: " + temp_dir)
            shutil.rmtree(temp_dir)

        if web_vu<MIN_WEB_VUS:
            logging.info("Checking out " + str(MIN_WEB_VUS) + " instead of just " + str(web_vu))
            web_vu = MIN_WEB_VUS

        print("Running an " + get_model_real_name(model) + " test using NeoLoad Web; since this is using remote resources, it may take a few moments to initialize")
        os.environ["NL_OPEN_BROWSER"] = "1"

        result = None

        f = io.StringIO()
        with redirect_stdout(f):
            ctx.invoke(cli_run.cli, name_or_id=nameorid, web_vu=str(web_vu), detached=True, scenario=scenario_name)
            logging.debug('Got stdout: "{0}"'.format(f.getvalue()))
            result = json.loads(f.getvalue())

        result_id = result["resultId"]

        wait(result_id, True)

        final_results = None

        f = io.StringIO()
        with redirect_stdout(f):
            summary(result_id)
            logging.debug('Got stdout: "{0}"'.format(f.getvalue()))
            final_results = json.loads(f.getvalue())

        outcomes = None

        if model == "ab":
            outcomes = get_ab_final_template()

        if outcomes is None:
            tools.system_exit({'message': "Unknown model, no output", 'code': 2})
            return
        else:
            for cat in ['result','statistics']:
                for key in final_results[cat]:
                    outcomes = outcomes.replace("{{test-"+cat+"-"+key+"}}",'{}'.format(final_results[cat][key]))

            if model == "ab":

                for key in ["response-header-server","request-tls-protocol","request-length"]:
                    outcomes = outcomes.replace("{{"+key+"}}", ab_parsed[key])

                outcomes = outcomes.replace("{{hostname}}", ab_parsed["hostname"])
                outcomes = outcomes.replace("{{port}}", '{}'.format(ab_parsed["port"]))
                outcomes = outcomes.replace("{{pathand}}", '{}'.format(ab_parsed["pathand"]))
                outcomes = outcomes.replace("{{concurrency}}", '{}'.format(ab_parsed["concurrency"]))

        print(outcomes)

def get_results_end_point(id_test: str = None, operation=''):
    slash_id_test = '' if id_test is None else '/' + id_test
    return rest_crud.base_endpoint_with_workspace() + __results_endpoint + slash_id_test + operation

def summary(__id):
    json_result = rest_crud.get(get_results_end_point(__id))
    json_stats = rest_crud.get(get_results_end_point(__id, __operation_statistics))
    tools.print_json({
        'result': json_result,
        'statistics': json_stats
    })

def handler(signal_received, frame):
    global __count
    if __current_id:
        inc = stop(__current_id, __count > 0, True)
        if inc:
            __count += 1

def wait(results_id, exit_code_sla):
    global __current_id
    __current_id = results_id
    signal(SIGINT, handler)
    running_tools.header_status(results_id)
    while running_tools.display_status(results_id):
        time.sleep(10)

    __current_id = None
    #tools.system_exit(test_results.summary(results_id), exit_code_sla)

def get_model_real_name(model):
    if model == "ab":
        return "ApacheBenchmark-like"
    else:
        return model

yaml_ext = ".nl.yaml"

def write_project_to_dir(alles,dirpath):

    # the cheap way
    artifacts = []
    artifacts.append({
        "rel": "default.yaml",
        "content": alles
    })

    # the convention-based way

    artifacts = []
    includes = []

    newalles = copy.deepcopy(alles)

    del newalles["user_paths"]
    for path in alles["user_paths"]:
        artifact = {
            "rel": "paths/"+path["name"]+yaml_ext,
            "content": { "user_paths": [path] }
        }
        artifacts.append(artifact)
        includes.append(artifact["rel"])

    del newalles["servers"]
    servers_rel = "servers/servers"+yaml_ext
    artifacts.append({
        "rel": servers_rel,
        "content": { "servers": alles["servers"] }
    })
    includes.append(servers_rel)

    newalles["includes"] = includes

    artifacts.append({
        "rel": "default.yaml",
        "content": newalles
    })

    for artifact in artifacts:
        filepath = os.path.join(dirpath, artifact["rel"])
        dirname = os.path.dirname(filepath)
        if not os.path.exists(dirname):
            os.makedirs(dirname)
        write_object_to_file(artifact["content"], filepath)

def write_object_to_file(obj,filepath):
    if os.path.exists(filepath):
        os.remove(filepath)
    with open(filepath, 'w+') as writer:
        writer.write(get_yaml_string(obj))

def get_yaml_string(obj):
    return yaml.dump(obj, default_flow_style=False, sort_keys=False)

def compose_ab(meta):
    parser = argparse.ArgumentParser()

    parser.add_argument('url')
    parser.add_argument('-n', dest='requests', default=1, type=int)
    parser.add_argument('-c', dest='concurrency', default=1, type=int)
    parser.add_argument('-t', dest='timelimit', default=None, type=int)
    parser.add_argument('-s', dest='timeout', default=30) # not supported directly, only by engine controller.properties
    parser.add_argument('-b', dest='windowsize', default=None) # not supported directly
    parser.add_argument('-B', dest='address', default=None) # not supported directly
    parser.add_argument('-p', dest='postfile', default=None)
    parser.add_argument('-u', dest='putfile', default=None)
    parser.add_argument('-T', dest='contenttype', default='text/plain')
    parser.add_argument('-v', dest='verbosity', default=2)
    parser.add_argument('-w', dest='printhtml', default=False) # not applicable
    parser.add_argument('-i', dest='usehead', default=False)
    parser.add_argument('-x', dest='attr_table', default=None) # not applicable
    parser.add_argument('-y', dest='attr_tr', default=None) # not applicable
    parser.add_argument('-z', dest='attr_td', default=None) # not applicable
    parser.add_argument('-C', dest='cookie', default=None)
    parser.add_argument('-H', dest='header', default=None)
    parser.add_argument('-A', dest='basic_auth', default=None)
    parser.add_argument('-P', dest='proxy_auth', default=None) # not applicable, only by engine controller.properties
    parser.add_argument('-X', dest='proxy_hostandport', default=None) # not applicable, only by engine controller.properties
    parser.add_argument('-V', dest='version')
    parser.add_argument('-k', dest='keepalive', default=None)
    parser.add_argument('-d', dest='nopercentiles', default=False) # not applicable
    parser.add_argument('-S', dest='noconfidence', default=False) # not applicable
    parser.add_argument('-q', dest='noprogress', default=False) # not applicable
    parser.add_argument('-l', dest='variablelengths', default=False) # not applicable
    parser.add_argument('-g', dest='output_gnuplotfile', default=None)
    parser.add_argument('-e', dest='output_percentiles', default=None)
    parser.add_argument('-r', dest='ignoresocketerrors', default=False) # not applicable
    parser.add_argument('-m', dest='method', default="GET")
    #parser.add_argument('-h', dest='help', default=False)
    parser.add_argument('-I', dest='sni', default=False) # not applicable
    parser.add_argument('-Z', dest='tls_ciphersuite', default=None) # not applicable, only by engine controller.properties
    parser.add_argument('-f', dest='tls_protocol', default=None) # not applicable, only by engine controller.properties

    args = parser.parse_args(meta.split())

    ret = { "args": args }

    # make a single initial request and stuff server header, tls, and req length into args for later use
    ret["response-header-server"] = "?"
    ret["request-tls-protocol"] = "?"
    ret["request-length"] = "?"

    if args.url:
        args.url = args.url.strip()

        if args.url[0] in ['"',"'"]:
            args.url = args.url[1:]
        if args.url[-1] in ['"',"'"]:
            args.url = args.url[:-1]

    duration = str(args.requests) + " iteration" + ("s" if args.requests > 1 else "")
    if args.timelimit is not None:
        args.requests = 50000
        duration = args.timelimit + "s"

    url = urlparse(args.url)
    pathand = "/" + "/".join(args.url.replace(url.scheme+"://","").split("/")[1:])

    #url.scheme|hostname|port
    server = {
        "name": url.hostname.replace(".","_") + ("_" + str(url.port) if url.port else ""),
        "scheme": url.scheme,
        "host": url.hostname,
    }
    if url.port and not url.port in [80,443]:
        server["port"] = url.port
    elif url.scheme == "http":
        server["port"] = 80
    elif url.scheme == "https":
        server['port'] = 443

    ret["hostname"] = url.hostname
    ret["port"] = url.port
    ret["pathand"] = pathand
    ret["concurrency"] = args.concurrency

    user_path = {
        "name": "Path1",
        "actions": {
            "steps": [
                {
                    "transaction": {
                        "name": "Transaction1",
                        "steps": [
                            {
                                "request": {
                                    "url": pathand,
                                    "server": server["name"]
                                }
                            }
                        ]
                    }
                }
            ]
        }
    }
    population = {
        "name": "Population1",
        "user_paths": [
            {
                "name": user_path["name"],
                "distribution": "100%"
            }
        ]
    }
    scenario = {
        "name": "Scenario1",
        "populations": [
            {
                "name": population["name"],
                "constant_load": {
                    "users": args.concurrency,
                    "duration": duration
                }
            }
        ]
    }

    project = {
        "name": "converted-ab-project",
        "servers": [server],
        "scenarios": [scenario],
        "populations": [population],
        "user_paths": [user_path],
    }

    ret["project"] = project

    return ret



def ab_print_help():
    str = """
Usage: ab [options] [http[s]://]hostname[:port]/path
Options are:
    -n requests     Number of requests to perform
    -c concurrency  Number of multiple requests to make at a time
    -t timelimit    Seconds to max. to spend on benchmarking
                    This implies -n 50000
    -s timeout      Seconds to max. wait for each response
                    Default is 30 seconds
    -b windowsize   Size of TCP send/receive buffer, in bytes
    -B address      Address to bind to when making outgoing connections
    -p postfile     File containing data to POST. Remember also to set -T
    -u putfile      File containing data to PUT. Remember also to set -T
    -T content-type Content-type header to use for POST/PUT data, eg.
                    'application/x-www-form-urlencoded'
                    Default is 'text/plain'
    -v verbosity    How much troubleshooting info to print
    -w              Print out results in HTML tables
    -i              Use HEAD instead of GET
    -x attributes   String to insert as table attributes
    -y attributes   String to insert as tr attributes
    -z attributes   String to insert as td or th attributes
    -C attribute    Add cookie, eg. 'Apache=1234'. (repeatable)
    -H attribute    Add Arbitrary header line, eg. 'Accept-Encoding: gzip'
                    Inserted after all normal header lines. (repeatable)
    -A attribute    Add Basic WWW Authentication, the attributes
                    are a colon separated username and password.
    -P attribute    Add Basic Proxy Authentication, the attributes
                    are a colon separated username and password.
    -X proxy:port   Proxyserver and port number to use
    -V              Print version number and exit
    -k              Use HTTP KeepAlive feature
    -d              Do not show percentiles served table.
    -S              Do not show confidence estimators and warnings.
    -q              Do not show progress when doing more than 150 requests
    -l              Accept variable document length (use this for dynamic pages)
    -g filename     Output collected data to gnuplot format file.
    -e filename     Output CSV file with percentages served
    -r              Don't exit on socket receive errors.
    -m method       Method name
    -h              Display usage information (this message)
    -I              Disable TLS Server Name Indication (SNI) extension
    -Z ciphersuite  Specify SSL/TLS cipher suite (See openssl ciphers)
    -f protocol     Specify SSL/TLS protocol
                    (TLS1, TLS1.1, TLS1.2 or ALL)
"""
    print(str)

def get_ab_final_template():
    return """
Benchmarking {hostname} (be patient).....done

Server Software:        {{response-header-server}}
Server Hostname:        {{hostname}}
Server Port:            {{port}}
SSL/TLS Protocol:       {{request-tls-protocol}}
TLS Server Name:        {{hostname}}

Document Path:          {{pathand}}
Document Length:        {{request-length}}

Concurrency Level:      {{concurrency}}
Time taken for tests:   {{test-result-duration}} ms
Complete requests:      {{test-statistics-totalRequestCountSuccess}}
Failed requests:        {{test-statistics-totalRequestCountFailure}}
Total transferred:      {{test-statistics-totalGlobalDownloadedBytes}} bytes
HTML transferred:       {{test-statistics-totalGlobalDownloadedBytes}} bytes
Requests per second:    {{test-statistics-totalRequestCountPerSecond}} [#/sec] (mean)
Time per request:       {{test-statistics-totalRequestDurationAverage}} [ms] (mean)
#Time per request:       {{test-statistics-totalRequestDurationAverage}} [ms] (mean, across all concurrent requests)
Transfer rate:          {{test-statistics-totalGlobalDownloadedBytesPerSecond}} [bytes/sec] received
"""
# Connection Times (ms)
#               min  mean[+/-sd] median   max
# Connect:      344  517  85.8    532     667
# Processing:   120  811 353.4   1001    1086
# Waiting:      120  796 345.2    995    1086
# Total:        473 1327 391.9   1495    1626

# Percentage of the requests served within a certain time (ms)
#   50%   ?
#   66%   ?
#   75%   ?
#   80%   ?
#   90%   ?
#   95%   ?
#   98%   ?
#   99%   ?
#  100%   ? (longest request)
#"""
