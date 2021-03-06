#!/usr/bin/env python
"""
Read API data directly via internet and output to pipe
"""

import sys, argparse, textwrap, requests, struct, json, logging
import sseclient
import pipe


# Header of the output data structure that the Blockstream Satellite Receiver
# generates prior to writing user data into the API named pipe
OUT_DATA_HEADER_FORMAT     = '64sQ'
OUT_DATA_DELIMITER         = 'vyqzbefrsnzqahgdkrsidzigxvrppato' + \
                             '\xe0\xe0$\x1a\xe4["\xb5Z\x0bv\x17\xa7\xa7\x9d' + \
                             '\xa5\xd6\x00W}M\xa6TO\xda7\xfaeu:\xac\xdc'
# Maximum transmission sequence number
MAX_SEQ_NUM = 2 ** 31


def create_output_data_struct(data):
    """Create the output data structure generated by the blocksat receiver

    The "Protocol Sink" block of the blocksat-rx application places the incoming
    API data into output structures. This function creates the exact same
    structure that the blocksat-rx application would.

    Args:
        data : Sequence of bytes to be placed in the output structure

    Returns:
        Output data structure as sequence of bytes

    """

    # Struct is composed of a delimiter and the message length
    out_data_header = struct.pack(OUT_DATA_HEADER_FORMAT,
                                  OUT_DATA_DELIMITER,
                                  len(data))

    # Final output data structure
    out_data = out_data_header + data

    return out_data


def fetch_api_data(server_addr, uuid):
    """Download a given message from the Ionosphere API

    Args:
        server_addr : Ionosphere API server address
        uuid        : Message unique ID

    Returns:
        Message data as sequence of bytes

    """
    logging.debug("Fetch message %s from API" %(uuid))
    r = requests.get(server_addr + '/order/' + uuid + '/sent_message')

    r.raise_for_status()

    if (r.status_code == requests.codes.ok):
        data        = r.content
        return data


def catch_up(pipe_f, server_addr, current_seq_num, last_seq_num):
    """Catch up with any transmission missed during re-connection

    During re-connection with the SSE server, events can be missed, depending on
    how quick the re-connection is handled. To catch up with missing data,
    observe the sequence number gap between the current transmission and the one
    previously received and fetch any missing data.

    Args:
        pipe_f          : Pipe object
        server_addr     : Ionosphere API server address
        current_seq_num : Current sequence number
        last_seq_num    : Sequence number of the previous message

    """

    # Missing messages (consider sequence number wrapping)
    if (current_seq_num < last_seq_num):
        # Unwrap
        current_seq_num  += MAX_SEQ_NUM
        # Range over unwrapped sequence numbers
        unwrapped_range   = range(last_seq_num + 1, current_seq_num)
        # Wrap back
        missing_num       = [(x % (MAX_SEQ_NUM)) for x in unwrapped_range]
    else:
        missing_num = range(last_seq_num + 1, current_seq_num)

    for seq_num in missing_num:

        logging.debug("Catch up with transmission %d" %(seq_num))
        r = requests.get(server_addr + '/message/' + str(seq_num))

        r.raise_for_status()

        if (r.status_code == requests.codes.ok):
            data = r.content

            print("%27s Get transmission - #%-5d - Size: %d bytes\t" %(
                "", seq_num, len(data)))

            # Frame in output data structure
            data_struct = create_output_data_struct(data)

            # Write to pipe
            pipe_f.write(data_struct)

            logging.debug("Output %d bytes to pipe %s" %(
                len(data_struct), pipe_f.name))


def main():
    parser = argparse.ArgumentParser(
        description=textwrap.dedent('''\
        Demo Receiver

        Receives data directly from the Ionosphere API though the internet and
        outputs the data to a named pipe just like the Blockstream Satellite
        receiver application (blocksat-rx) would.

        '''),
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument('-f', '--file',
                        default='/tmp/blocksat/api',
                        help='Pipe on which API data is received ' +
                        '(default: /tmp/blocksat/api)')
    parser.add_argument('-p', '--port',
                        default=None,
                        help='Ionosphere API server port (default: None)')
    parser.add_argument('-s', '--server',
                        default='https://satellite.blockstream.com',
                        help='Ionosphere API server address (default: ' +
                        'https://satellite.blockstream.com)')
    parser.add_argument('--debug', action='store_true',
                        help='Debug mode (default: false)')
    args        = parser.parse_args()
    pipe_file   = args.file
    port        = args.port
    server      = args.server

    # Switch debug level
    if (args.debug):
        logging.basicConfig(stream=sys.stderr, level=logging.DEBUG)
        logging.debug('[Debug Mode]')

    # Process the server address
    server_addr = server

    if (port is not None):
        server_addr = server + ":" + port

    if (server_addr == 'https://satellite.blockstream.com'):
        server_addr += '/api'

    # Open pipe
    pipe_f = pipe.Pipe(pipe_file)

    # Always keep a record of the last received sequence number
    last_seq_num = None

    print("Waiting for events...\n")
    while (True):
        try:
            # Server-side Event Client
            client = sseclient.SSEClient(requests.get(server_addr +
                                                      "/subscribe/transmissions",
                                                      stream=True))

            # Continuously wait for events
            for event in client.events():
                # Parse the order corresponding to the event
                order = json.loads(event.data)

                # Debug
                logging.debug("Order: " + json.dumps(order, indent=4,
                                                     sort_keys=True))

                # Download the message only if its order has "sent" state
                if (order["status"] == "sent"):
                    # Sequence number
                    seq_num = order["tx_seq_num"]

                    # On a sequence number gap, catch up with missing messages
                    if (last_seq_num is not None):
                        expected_seq_num = (last_seq_num + 1) % (MAX_SEQ_NUM)

                        if (seq_num != expected_seq_num):
                            catch_up(pipe_f, server_addr, seq_num, last_seq_num)

                    print("[%s]: New transmission - #%-5d - Size: %d bytes\t" %(
                        order["upload_ended_at"], seq_num,
                        order["message_size"]))

                    # Get the data
                    data = fetch_api_data(server_addr, order["uuid"])

                    # Output to named pipe
                    if (data is not None):
                        data_struct = create_output_data_struct(data)
                        pipe_f.write(data_struct)
                        logging.debug("Output %d bytes to pipe %s" %(
                            len(data_struct), pipe_f.name))

                    # Record the sequence number of the order that was received
                    last_seq_num = seq_num

        except requests.exceptions.ChunkedEncodingError:
            print("Reconnecting...")
            pass


if __name__ == '__main__':
    main()
