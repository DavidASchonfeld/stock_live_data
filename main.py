
# Python Libraries
import os
from typing import Any, Dict

import requests
import json

from urllib.parse import urlencode, urljoin
import copy

import pandas as pd
from pandas import DataFrame



# My Files
from api_key import api_keys
from api_urls import open_weather


def sendRequest_openMeteo():
    base_url = "https://api.open-meteo.com/v1/forecast"
    parameters = {
        "latitude": 40,
        "longitude" : 40,
        "hourly" : "temperature_2m",
        "temperature_unit" : "fahrenheit"
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

dictGotten : dict = sendRequest_openMeteo()
print(dictGotten)
DataFrameGotten : pd.DataFrame = pd.DataFrame(dictGotten)
print("--------")
print(DataFrameGotten)
