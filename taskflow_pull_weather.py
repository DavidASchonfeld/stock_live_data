# General Libraries

import json

import pendulum

from airflow.decorators import dag, task
from airflow.models.xcom_arg import XComArg

from typing import Annotated, Any

# My Files
from api_weather_requests import sendRequest_openMeteo




@dag(
    schedule=None,
    start_date=pendulum.datetime(2025, 6, 4, tz="UTC"),
    catchup=False,
    tags=["learning"]
)
def someThing():
    """
    # TODO: Comment
    """

    @task()
    def extract():
        """
        ### Extract:
        Pull information from Meteo Website
        """

        dictGotten : dict = sendRequest_openMeteo(inLatitude=40, inLongitude=40, inFarenheit=True)
        print(dictGotten)
        return dictGotten
    
    # @task(multiple_outputs=True) 
    #   Only best used if downstream (tasks after this one) tasks need to use different parts of the outputted dictionary-like object.
    #   Returns a dictioanry-like object, separating top level key-value pairs into different XComArg objects
    #   To access the results, it would be similar to accessing dictionary values. For example: load(stuff, transformed["timestamp"])

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
someThing()