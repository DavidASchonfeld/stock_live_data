
# Python Libraries
import os
from typing import Any, Dict

import requests
import json

from urllib.parse import urlencode, urljoin
import copy

import pandas as pd
from pandas import DataFrame
from outputTextWriter import OutputTextWriter



# My Files
from api_key import api_keys  # My api_key Python file is in .gitignore
from api_urls import open_weather


def sendRequest_openMeteo(inLatitude : int, inLongitude: int, inFarenheit : bool) -> dict:
    base_url = "https://api.open-meteo.com/v1/forecast"

    measurement_tool : str = "fahrenheit" if inFarenheit else "celcius"
    parameters = {
        "latitude": inLatitude,
        "longitude" : inLongitude,
        "hourly" : "temperature_2m",
        "temperature_unit" : measurement_tool
    }
    try:
        response : requests.Response = requests.get(base_url, params = parameters)
        response.raise_for_status()
    except requests.exceptions.HTTPError:
        print("response.status_code: "+str(response.status_code))
        raise requests.exceptions.HTTPError("Response Status Code: "+str(response.status_code))
    except Exception as error:
        pass

    responseContent_dict : dict = json.loads(response.content)



    return responseContent_dict

# Only runs if this script is called directly, not if this script is imported
if __name__ == "__main__":


    dictGotten : dict = sendRequest_openMeteo(inLatitude=40, inLongitude=40, inFarenheit=True)
    print(dictGotten)
    DataFrameGotten : pd.DataFrame = pd.DataFrame(dictGotten)
    print("--------")
    print(DataFrameGotten)
    inputTextWriter : OutputTextWriter = OutputTextWriter()
    inputTextWriter.print_dict(dictGotten, True)

    ### Kafka.
    #### On server, use the follow command: 
    ## Start the Kafka handler/system/server Zookeeper
    # bin/kafka-topics.sh --create -zookeeper localhost:2181 --replication-factor 1 --partitions 1 --topic topic-name

    ## Start a topic (like a Stack) to handle data

    ## Get List of Topics:
    # bin/kafka-topics.sh --list --zookeeper localhost:2181


    # from confluent_kafka import Producer

    # def errorMessage_produce(inError, inMessage):
    #     if (inError):
    #         print("Produce: Message Failed. inMessage: "+str(inMessage))
    #     else:
    #         print("Produce: Topic "+str(inMessage.topic())+"Message Sent: "+str(inMessage))
    #         pass
    #     print(inMessage.topic())


    # p = Producer({'bootstrap.servers':'localhost:9092'})

    # try :
    #     p.produce('weather_info_testing', key='someKey', value='Test1', callback = errorMessage_produce)
    #     p.flush(10)  # 10 Second Timeout
    # except Exception as e:
    #     print("Connection to Kafka failed")
    # finally:
    #     p.flush(1) #Ensure messages are sent....
