
# Python Libraries
import requests
import json

import pandas as pd
from file_logger import OutputTextWriter  # renamed from outputTextWriter


# Removed: from api_key import api_keys — Open-Meteo is free/keyless; import was unused leftover from OpenWeatherMap era
# Removed: from api_urls import open_weather — api_urls.py is archived; open_weather was never used by fetch_weather_forecast


# ── Why Open-Meteo? ───────────────────────────────────────────────────────────
# Open-Meteo is a free, open-source weather API that requires no API key.
# It returns 7 days of hourly forecasts by default (168 rows: 7 days × 24 hours).
# The response shape used downstream:
#   {
#     "latitude": ..., "longitude": ..., "elevation": ...,
#     "timezone": ..., "utc_offset_seconds": ...,
#     "hourly": {
#       "time": ["2025-01-01T00:00", ...],      # ISO-8601 timestamps, one per hour
#       "temperature_2m": [32.5, 31.0, ...]     # temperature at 2 metres height
#     }
#   }
# ─────────────────────────────────────────────────────────────────────────────
def fetch_weather_forecast(latitude: float, longitude: float, fahrenheit: bool) -> dict:  # renamed from sendRequest_openMeteo; params renamed from inLatitude/inLongitude/inFarenheit
    """
    ### Fetch hourly weather forecast from Open-Meteo (free, no API key needed).

    Parameters
    ----------
    latitude  : Latitude of the location to query (decimal degrees)
    longitude : Longitude of the location to query (decimal degrees)
    fahrenheit : True = return temperatures in Fahrenheit, False = Celsius

    Returns
    -------
    Raw JSON response as a dict (see shape in the block comment above).
    """
    base_url = "https://api.open-meteo.com/v1/forecast"

    measurement_tool : str = "fahrenheit" if fahrenheit else "celsius"
    parameters = {
        "latitude": latitude,
        "longitude" : longitude,
        "hourly" : "temperature_2m",
        "temperature_unit" : measurement_tool
    }
    try:
        response : requests.Response = requests.get(base_url, params = parameters)
        response.raise_for_status()
    except requests.exceptions.HTTPError as error:
        print(f"response.status_code: {response.status_code}")
        raise requests.exceptions.HTTPError(f"Response Status Code: {response.status_code}")
    except Exception as error:
        print(f"API request failed: {str(error)}")
        raise

    responseContent_dict : dict = json.loads(response.content)

    return responseContent_dict

# Only runs if this script is called directly, not if this script is imported
if __name__ == "__main__":

    raw_data : dict = fetch_weather_forecast(latitude=40, longitude=40, fahrenheit=True)
    print(raw_data)
    df : pd.DataFrame = pd.DataFrame(raw_data)
    print("--------")
    print(df)
    inputTextWriter : OutputTextWriter = OutputTextWriter()
    inputTextWriter.print_dict(raw_data, True)
