# General Libraries

import json
from typing import Annotated, Any
from datetime import datetime, timedelta

import pendulum

from airflow.decorators import dag, task
from airflow.models.xcom_arg import XComArg




# default_args = 

@dag(  # type:ignore
    "TESTDaveJune122025",
    default_args={
        # Brought these "default_args" section from Airflow tutorial codes
        # [START default_args]
        # These args will get passed on to each operator
        # You can override them on a per-task basis during operator initialization
        "depends_on_past": False,
        "retries": 1,
        "retry_delay": timedelta(minutes=5),
        # 'queue': 'bash_queue',
        # 'pool': 'backfill',
        # 'priority_weight': 10,
        # 'end_date': datetime(2016, 1, 1),
        # 'wait_for_downstream': False,
        # 'execution_timeout': timedelta(seconds=300),
        # 'on_failure_callback': some_function, # or list of functions
        # 'on_success_callback': some_other_function, # or list of functions
        # 'on_retry_callback': another_function, # or list of functions
        # 'sla_miss_callback': yet_another_function, # or list of functions
        # 'on_skipped_callback': another_function, #or list of functions
        # 'trigger_rule': 'all_success'
        # [END default_args]
    },
    description="Pulling weather info from Meteo Weather API",
    schedule=timedelta(minutes=2,days=0),   #timedelta(days=1),
    start_date=pendulum.datetime(2025, 6, 7, 19, 29, tz="America/New_York"),
    # Note: start_date has to be in the past if you want it to run today/later
    catchup=False,
    tags=["learning","weather","external api pull"]
)

def zero_nameThatAirflowUIsees(): #nameThatAirflowUIsees if I don't specify a name in the @dag section above
    """
    # TODO: Comment
    """

    @task()
    def extract():
        """
        ### Extract:
        Pull information from Meteo Website
        """

        result : dict[str, str] = {"Data": "To implement"}
        print(result)
        return result
    
    
    # @task(multiple_outputs=True) 
    #   Only best used if downstream (tasks after this one) tasks need to use different parts of the outputted dictionary-like object.
    #   Returns a dictioanry-like object, separating top level key-value pairs into different XComArg objects
    #   To access the results, it would be similar to accessing dictionary values. For example: load(stuff, transformed["timestamp"])
    @task
    def transform(theData: Annotated[XComArg, dict[str, Any]]):
        """
        ### Transform task. aka clean/format the data
        """

        return theData
    @task()
    def load(inData):
        """
        ### Load Task
        Push to the storage
        (In this case, push to Kafka topic,
        and a different script will take
        the data from the kafka topic
        and put it into the SQL database))
        """

        print(str(inData))
    
    # Airflow auomatically converts all tsak method outputs to XComArg objects.
    # If I want the objects treated as the types I want 

    order_data : XComArg = extract()
    order_summary : XComArg = transform(order_data)
    load(order_summary)
# zero_nameThatAirflowUIsees()
dag = zero_nameThatAirflowUIsees() 