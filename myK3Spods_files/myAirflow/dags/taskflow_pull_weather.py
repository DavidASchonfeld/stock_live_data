# General Libraries

import json
from typing import Annotated, Any, cast
from datetime import datetime, timedelta

import pendulum

from airflow.decorators import dag, task
from airflow.models.xcom_arg import XComArg


import pandas as pd
from sqlalchemy import create_engine
from sqlalchemy.exc import SQLAlchemyError


# My Files
from api_weather_requests import sendRequest_openMeteo
from outputTextWriter import OutputTextWriter



# default_args = 

@dag(  # type:ignore
    "API_Weather-Pull_Data",
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
    # start_date=pendulum.datetime(2025, 6, 22, 13, 10, tz="America/New_York"),
    start_date=pendulum.now("America/New_York").subtract(minutes=3),
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

        dictGotten : dict = sendRequest_openMeteo(inLatitude=40, inLongitude=40, inFarenheit=True)
        print(dictGotten)
        return dictGotten
    
    
    # @task(multiple_outputs=True) 
    #   Only best used if downstream (tasks after this one) tasks need to use different parts of the outputted dictionary-like object.
    #   Returns a dictioanry-like object, separating top level key-value pairs into different XComArg objects
    #   To access the results, it would be similar to accessing dictionary values. For example: load(stuff, transformed["timestamp"])
    @task
    # def transform(inData: Annotated[XComArg, dict[str, Any]]):
    def transform(inData):
        # Not adding type hinting since type hinting for
        # XComArg causes issues when importing the data into a Pandas dataframe
        inData = cast(dict[str, Any], inData)
        """
        ### Transform task. aka clean/format the data
        """

        # Location that the K3S Kubernetes pod (as specified in the PortableVolume) is pointing to inside the K3S Kubernetes pod, which will push 
        writer : OutputTextWriter = OutputTextWriter("/opt/airflow/out")

        # Transform the incoming JSON into a SQL table, each with a different row for each time (and therfore smae or different temperature)
        # I will need to add
        # --- a primary key
        # --- time of import
        # --- MAYBE: If the input is only about latitude/longitude, maybe I can add a city name?...
        # ------But maybe a city is larger than 1 latitude/longitude
        inData_dataframe = pd.DataFrame(inData)


        newDataFrame : pd.DataFrame = pd.json_normalize(
            inData_dataframe,

            # Currently Editing
            record_path = ['hourly', 'temperature_2m'],
            record_path = ['hourly', 'time'],


            meta = [
                'elevation',
                'generationtime_ms',
                'hourly_units' <-2 JSON objects,
                'latitude',
                'longitude',
                'timezone',
                'timezone_abbreviation',
                'utc_offset_seconds'
            ]
        )

        datetime.now()




        return inData
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
        # Location of Logs
        # writer : OutputTextWriter = OutputTextWriter("/home/ec2-user/myK3Spods_files/myAirflow/dag-mylogs")

        # Location that the K3S Kubernetes pod (as specified in the PortableVolume) is pointing to inside the K3S Kubernetes pod, which will push 
        writer : OutputTextWriter = OutputTextWriter("/opt/airflow/out")


        # NOTE: I must declare this inside a @task object so the task only connects to that folder when the task runs.
        # If I had declared this constructor in the main area (outside of a task method etc.), it would run whe nthe DAG is initalized,
        # which would cause issues.

        print(str(inData))
        writer.print_dict(inData, True)

        #TODO: Move this conversion to Pandas Dataframe object
        # myDataFrameThing = pd.DataFrame([inData])
        myDataFrameThing = pd.DataFrame(inData)

        ## Testing/Learning about Python to SQL (with Pandas)
        
        

        try:
            # engine = create_engine("mysql+pymysql://USERNAME:PASSWORD@localhost:3306/mydatabase")
            # If Apache Airflow was not inside Kubernetes pod, since MariaDB is already outside a pod: "localhost:3306"  # Default MariaDB Value (This Command in "Command Line" confirms this: "sudo netstat -tulnp | grep 3306")

            sql_username = "airflow_user"
            sql_password = "8*Gorilla*8"
            sql_database = "database_one"  # Default MariaDB Value
            sql_urlLocation = "172.31.23.236" #"local IP"
        
            engine = create_engine("mysql+pymysql://"+sql_username+":"+sql_password+"@"+sql_urlLocation+"/"+sql_database)
            
            with engine.connect() as connection:
                result_one = connection.execute("SELECT 1")
                print("Success! "+str(result_one.scalar()))
        except SQLAlchemyError as e:
            print("Connection failed. Error: "+str(e))

        # def flattenDictIntoJSON(inCell):
        #     return "horshoe"
        # myDataFrameThing : pd.DataFrame = myDataFrameThing.applymap(flattenDictIntoJSON)

        
        # Still have to install MySQL etc. the d
        # Would need "pip install pymysql"
        # Also, URL for SQL might be different since this script will be in kuberentes pod
        # and my SQL db will be outside the Kubernetes pod


        writer.print("----AAA----")
        writer.print(str(myDataFrameThing.head()))
        writer.print(str(myDataFrameThing.dtypes))
        writer.print("----BBB----")

        ### TODO: UNCOMMENT THIS.
        ### THIS LINE PUTS THE STUFF INTO SQL DATABASE, AUTOAMTICALLY CONVERTING IT INTO A SQL OBJECT
        # myDataFrameThing.to_sql("nameForTable", con = engine, if_exists="append", index=False)

        # index = False means: don't write the Pandas Dataframe's index into the SQL table



    
    # Airflow auomatically converts all tsak method outputs to XComArg objects.
    # If I want the objects treated as the types I want 

    order_data : XComArg = extract()
    order_summary : XComArg = transform(order_data)
    load(order_summary)
zero_nameThatAirflowUIsees()