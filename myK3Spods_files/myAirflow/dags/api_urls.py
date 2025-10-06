
# API Url Information



open_weather = {
    "base_url_geo" : "http://api.openweathermap.org/",
    "base_url" : "https://api.openweathermap.org/",
    "current_weather" : {
        "base" : "data/3.0/onecall",
        "params" : {
            "lat" : 0, #f"{lat}",
            "lon" : 0, #f"{lon}"
            # lat=33.44&lon=-94.04&appid={API key}
        }
    }
}


# One Call API 3.0
# Info: https://openweathermap.org/api/one-call-3
# https://api.openweathermap.org/data/3.0/onecall?lat=33.44&lon=-94.04&appid={API key}