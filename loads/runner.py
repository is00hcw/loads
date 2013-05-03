# runs a functional test or a load test
import unittest
import argparse
import sys
import time
import json

from gevent.pool import Group
import gevent

from loads.util import resolve_name
from loads.stream import set_global_stream, stream_list, StdStream
from loads import __version__
from loads.client import LoadsClient
from loads.transport.util import DEFAULT_FRONTEND


def _run(num, test, test_result, cycles, user):
    for cycle in cycles:
        test.current_cycle = cycle
        test.current_user = user
        for x in range(cycle):
            test(test_result)
            gevent.sleep(0)


def _compute(args):
    users = [int(user) for user in args['users'].split(':')]
    cycles = [int(cycle) for cycle in args['cycles'].split(':')]
    agents = args['agents']
    total = 0
    for user in users:
        total += sum([cycle * user for cycle in cycles])
    if agents is not None:
        total *= agents
    return total, cycles, users, agents


def run(args):
    """ Runs a test.
    """
    from gevent import monkey
    monkey.patch_all()

    total, cycles, users, agents = _compute(args)
    stream = args['stream']

    if stream == 'stdout':
        args['stream_stdout_total'] = total

    set_global_stream(stream, args)
    test = resolve_name(args['fqn'])
    klass = test.im_class
    ob = klass(test.__name__)
    test_result = unittest.TestResult()

    for user in users:
        group = Group()

        for i in range(user):
            group.spawn(_run, i, ob, test_result, cycles, user)

        group.join()

    return  test_result


def distributed_run(args):
    # in distributed mode the stream is forced to 'zmq'
    args['stream'] = 'zmq'
    set_global_stream('zmq', args)
    total, cycles, users, agents = _compute(args)

    # setting up the stream of results
    #
    import zmq.green as zmq
    from zmq.green.eventloop import ioloop, zmqstream

    context = zmq.Context()
    pull = context.socket(zmq.PULL)
    pull.bind(args['stream_zmq_endpoint'])

    # calling the clients now
    client = LoadsClient(args['broker'])
    workers = client.run(args)

    # local echo
    echo = StdStream({'stream_stdout_total': total})

    # io loop
    received = [0]
    loop = ioloop.IOLoop()

    def recv_result(msg):
        data = json.loads(msg[0])
        echo.push(data)
        received[0] += 1
        if received[0] == total:
            loop.stop()

    stream = zmqstream.ZMQStream(pull, loop)
    stream.on_recv(recv_result)
    loop.start()


def main():
    parser = argparse.ArgumentParser(description='Runs a load test.')
    parser.add_argument('fqn', help='Fully qualified name of the test',
                         nargs='?')

    parser.add_argument('-u', '--users', help='Number of virtual users',
                        type=str, default='1')

    parser.add_argument('-c', '--cycles', help='Number of cycles per users',
                        type=str, default='1')

    parser.add_argument('--version', action='store_true', default=False,
                        help='Displays Loads version and exits.')

    parser.add_argument('-a', '--agents', help='Number of agents to use',
                        type=int)
    parser.add_argument('-b', '--broker', help='Broker endpoint',
                        default=DEFAULT_FRONTEND)

    streams = [st.name for st in stream_list()]
    streams.sort()

    parser.add_argument('--stream', default='stdout',
                        help='The stream that receives the results',
                        choices=streams)


    # per-stream options
    for stream in stream_list():
        for option, value in stream.options.items():
            help, type_, default, cli = value
            if not cli:
                continue

            kw = {'help': help, 'type': type_}
            if default is not None:
                kw['default'] = default

            parser.add_argument('--stream-%s-%s' % (stream.name, option),
                                **kw)

    args = parser.parse_args()

    if args.version:
        print(__version__)
        sys.exit(0)

    if args.fqn is None:
        parser.print_usage()
        sys.exit(0)

    args = dict(args._get_kwargs())

    if args.get('agents') is None:
        # direct run
        #
        result = run(args)
        print
        print result
    else:
        # distributed run
        # contact the broker and send the load
        result = distributed_run(args)
        print
        print result

if __name__ == '__main__':
    main()
