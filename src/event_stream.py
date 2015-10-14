from contextlib import contextmanager
import datetime
import json
import logging
from kafka import KafkaConsumer
from kafka.common import ConsumerTimeout
import sys


def create_stream_generator(kafka_pool, topic, partition, start_from, opts):

    start = datetime.datetime.now()

    # stream
    def generator():
        messages_read = 0
        keep_alive_in_a_row = 0
        current_batch = []
        batch_start_time = datetime.datetime.now()

        with kafka_pool.kafka_client() as client:

            consumer = KafkaConsumer((topic, partition, start_from),
                         kafka_client = client,
                         auto_commit_enable = False,
                         consumer_timeout_ms = 200)

            while True:
                try:
                    message = consumer.next()

                    # if we read the message - reset keep alive counter
                    keep_alive_in_a_row = 0

                    # put message to batch
                    messages_read += 1
                    message_json = json.loads(message.value.decode('utf-8'))
                    current_batch.append(message_json)

                except ConsumerTimeout:
                    pass

                # check if it's time to send the batch
                time_since_batch_start = datetime.datetime.now() - batch_start_time
                if time_since_batch_start.total_seconds() >= opts['batch_flush_timeout'] != 0 or len(current_batch) >= opts['batch_limit']:

                    # send the messages we could read so far
                    if len(current_batch) > 0:
                        stream_message = __create_stream_message(partition, start_from + messages_read, current_batch)
                        with __measure_time(current_batch, stream_message):
                            yield stream_message

                    # just send the keep alive
                    else:
                        # if we hit keep alive count limit - close the stream
                        if keep_alive_in_a_row >= opts['batch_keep_alive_limit'] != -1:
                            break

                        # send keep alive message
                        keep_alive_in_a_row += 1
                        yield __create_stream_message(partition, start_from + messages_read)

                    # init new batch
                    current_batch = []
                    batch_start_time = datetime.datetime.now()

                # check if we reached the stream timeout or message count limit
                time_since_start = datetime.datetime.now() - start
                if time_since_start.total_seconds() >= opts['stream_timeout'] > 0 or 0 < opts['stream_limit'] <= messages_read:

                    # flush what we have read so far (if any) and close the stream
                    if len(current_batch) > 0:
                        stream_message = __create_stream_message(partition, start_from + messages_read, current_batch)
                        with __measure_time(current_batch, stream_message):
                            yield stream_message
                    break

    return generator()


def __create_stream_message(partition, offset, events = None, topology = None):
    message = {
        'cursor': {
            'partition': str(partition),
            'offset': str(offset)
        }
    }
    if events is not None:
        message['events'] = events
    if topology is not None:
        message['topology'] = topology
    return json.dumps(message) + '\n'


@contextmanager
def __measure_time(batch, message):

    message_size_bytes = sys.getsizeof(message)
    batch_length = len(batch)

    before = datetime.datetime.now()

    yield

    after = datetime.datetime.now()
    delta =  after - before
    ms = int(delta.total_seconds() * 1000)

    logging.info("[#BAND_CHECK] Batch size: %s; Size in bytes: %s; Ms spent on yielding: %s", batch_length, message_size_bytes, ms)
