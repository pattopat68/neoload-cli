# neoload compose
The NeoLoad CLI can create test assets as well as run tests.

## Apache Benchmark 'like' tests
Though tools like Apache Benchmark can be useful on a single workstation or in
 the context of simple CI workflows, it is constrained to the resources and environments
 it is run on. This is fine for low-volume API throughput tests and fast, but often
 falls short in terms of larger and longer API tests.

To escape these limits and provide an easy path from simple ab usage on a workstation
 to more formal testing processes, you can copy and paste your Apache Benchmark
 command line arguments directly into the NeoLoad CLI 'compose ab' subcommand thusly:

```
neoload compose ab '-n 12 -c 3 "https://nominatim.openstreetmap.org/search?format=json&q=Boston"'
```

However, this really doesn't do much unless you're A) converting this to NeoLoad as-code YAML,
 or B) running it with NeoLoad Web. Therefore, it makes more sense to do one of the following:

```
neoload compose --write-to ./default.yaml \
       ab '-n 12 -c 3 "https://nominatim.openstreetmap.org/search?format=json&q=Boston"'
```
```
neoload compose --write-to ./somefolder \
       ab '-n 12 -c 3 "https://nominatim.openstreetmap.org/search?format=json&q=Boston"'
```

Note: if you are overwriting an existing file or directory, you will also need to supply
 the '--overwrite' argument to the above commands.

This will create YAML scripts ready to run with NeoLoad Web from the ab details provided.

## Running the test immediately and reviewing results
If you are already logged in to the NeoLoad CLI, you can use an existing test (test-settings)
 and infrastructure zone to run this test.
```
neoload compose --upload-and-run-as cur \
       ab '-n 12 -c 3 "https://nominatim.openstreetmap.org/search?format=json&q=Boston"'
```

Note: you will need to have a test in NeoLoad Web already created and attached to
 an infrastructure zone with at least one available controller and load generator.

It may make sense to preface the above command by making sure your CLI is pointing to
 an existing test with something like:
```
neoload test-settings --zone DefaultZone --lgs 1 createorpatch "MyTest"
```

Output:

```
paul$ neoload compose --upload-and-run-as cur ab '-n 12 -c 3 "https://nominatim.openstreetmap.org/search?format=json&q=Boston"'
Running an ApacheBenchmark-like test using NeoLoad Web; since this is using remote resources, it may take a few moments to initialize
Results of  : 8ee76ad1-08a7-4c41-ace2-cd19c82450c4
Logs are available at https://neoload.saas.neotys.com#!result/8ee76ad1-08a7-4c41-ace2-cd19c82450c4/overview
Status: INIT
Status: RUNNING
    00:00:03/ - 	 Err[--], LGs[1]	 VUs:3	 BPS[5157.709]	 RPS:1.996	 avg(rql): 1194
    00:00:14/ - 	 Err[--], LGs[1]	 VUs:3	 BPS[8881.016]	 RPS:2.000	 avg(rql): 1447
Status: TERMINATED

Benchmarking {hostname} (be patient).....done

Server Software:        ?
Server Hostname:        nominatim.openstreetmap.org
Server Port:            None
SSL/TLS Protocol:       ?
TLS Server Name:        nominatim.openstreetmap.org

Document Path:          /search?format=json&q=Boston
Document Length:        ?

Concurrency Level:      3
Time taken for tests:   19017 ms
Complete requests:      36
Failed requests:        0
Total transferred:      183960 bytes
HTML transferred:       183960 bytes
Requests per second:    1.893043 [#/sec] (mean)
Time per request:       1458.3611 [ms] (mean)
#Time per request:       1458.3611 [ms] (mean, across all concurrent requests)
Transfer rate:          9673.45 [bytes/sec] received
```
