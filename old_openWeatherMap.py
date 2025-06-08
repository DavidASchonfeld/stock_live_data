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
from api_key import api_keys  # My api_key Python file is in .gitignore
from api_urls import open_weather


# http://api.openweathermap.org/geo/1.0/direct?q={city name},{state code},{country code}&limit={limit}&appid={API key}


# def sendRequest_openWeather(endStringRequest : str) -> Dict[Any, Any]:
    

#     params = copy.deepcopy(open_weather["current_weather"]["params"])
#     params["lat"] = 33.44
#     params["lon"] = -94.04
#     params["appid"] = api_keys.open_weather["key"]

#     params_formatted : str = urlencode(params)

#     # Merge End String
#     endUrlRequest :str = open_weather["current_weather"]["base"] + "?" + params_formatted

#     requestToSend_full : str = urljoin(open_weather["base_url"], endUrlRequest)
#     print("requestToSend_full: "+str(requestToSend_full))
    
#     try:
#         response : requests.Response = requests.get(requestToSend_full)
#         response.raise_for_status()
#     except requests.exceptions.HTTPError:
#         print("response.status_code: "+str(response.status_code))
#         raise requests.exceptions.HTTPError("Response Status Code: "+str(response.status_code))
#     except Exception as error:
#         pass

#     responseContent_dict : dict = json.loads(response.content)

#     return responseContent_dict

# sendRequest("Test. This string's value is not yet implemented")
