import os
from dotenv import load_dotenv
from fastmcp import FastMCP
import httpx

gaode_mcp = FastMCP(name = "gaode_map_mcp",
              instructions = "To interact with Gaode Map API. Every tool could return structured result.")

gaode_mcp.enable(tags = {"agent"} , only = True)

load_dotenv()
API_KEY = os.getenv("GAODE_MAP_API_KEY")

from gaode_map_config import (DEFAULT_HEADERS , DEFAULT_TIMEOUT)


SUCCESS_STR = "Request Succeeded."
FAILURE_STR = "Request Failed."

from typing import Any
from dataclasses import dataclass

def Api_result(result_status : str , result_content:Any )-> dict:
    return { "result_status" : result_status,
            "result_content" : result_content}

async def api_request(url: str, params: dict) -> dict:
    """统一 API 请求方法"""
    async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT, headers=DEFAULT_HEADERS) as client:
        params["key"] = API_KEY
        response = await client.get(url, params=params)

        try:
            response.raise_for_status()
        except Exception as e:
            return Api_result(FAILURE_STR , str(e) + "during api_request")
        
        try:
            result_content = response.json()
        except Exception as e:
            return Api_result(FAILURE_STR , str(e) + "during api_request")
        
        return Api_result(SUCCESS_STR , result_content)
    
from typing import Annotated
import asyncio

########################################### geocoding ###########################################
AMAP_GEO_URL = os.getenv("AMAP_GEO_URL")
@gaode_mcp.tool(tags = {"agent"})
async def geocoding(
    address:Annotated[ str , "要查询 地理编码 的地址，需要输入结构化地址信息，如：北京市朝阳区阜通东大街6号。"],
    city:Annotated[ str | None, "指定查询的城市，不支持县级市。输入时不包括城市后缀，例如，应该使用“北京”，而不是“北京市”"] = None
)-> dict:
    """
    地理编码工具：
    输入地址，提取和查询地址的：
        城市编码(citycode)，例如，北京是“010”；
        区域编码(adcode)，例如北京市朝阳区是“110101”；
        经纬度(location)，经度在前，维度在后。例如北京市朝阳区阜通东大街6号是“116.482086,39.990496”
    注意：可能会返回多个匹配结果
    """
    trimmed_result = {}

    params = {"address" : address}
    if city:
        params["city"] = city

    result = await api_request(AMAP_GEO_URL,params)
    if result["result_status"] == FAILURE_STR:
        return result
    if result["result_content"]["status"] == "0":
        return Api_result( FAILURE_STR , result["result_content"]["info"] ) 
    elif result["result_content"]["status"] == "1":
        result_collect =  result["result_content"]["geocodes"]
        for i in range(len(result_collect)):
            key_here = f"geocoding result no.{str(i)}"
            trimmed_result[key_here] = {}
            trimmed_result[key_here]["citycode"] = result_collect[i]["citycode"]
            trimmed_result[key_here]["adcode"] = result_collect[i]["adcode"]
            trimmed_result[key_here]["location"] = result_collect[i]["location"]
        return trimmed_result
# #test
# cool_result  = asyncio.run(geocoding("北京市朝阳区阜通东大街6号"))
# print(cool_result)



######################################### public_transit_route_planning #########################################
def remove_specific_dict_key(original_data:Any,  keys_to_remove :set[str]) -> Any:
    """递归删除所有字典中的 'via_stops' 键，返回新结构"""
    if isinstance(original_data, dict):
        # 先创建新字典，过滤掉 'via_stops'，然后递归处理值
        return {
            key: remove_specific_dict_key(value, keys_to_remove)
            for key, value in original_data.items()
            if key not in keys_to_remove
        }
    elif isinstance(original_data, list):
        return [remove_specific_dict_key(item, keys_to_remove) for item in original_data]
    else:
        return original_data

AMAP_PUBLIC_TRANSIT_URL = os.getenv("AMAP_PUBLIC_TRANSIT_URL")
@gaode_mcp.tool(tags = {"agent"})
async def public_transit_route_planning(
    origin: Annotated[ str, "路线规划起点的经纬度，经度在前，纬度在后，经度和纬度用,分割，经纬度小数点后不得超过6位。例如：“116.482086,39.990496”" ],
    destination: Annotated[str , "路线规划终点的经纬度，格式要求与起点(origin)相同。"],
    city1:Annotated[ str , "起点所在的城市。注意，只接受城市编码(citycode)，例如，北京的话就是“010”。拿不准请通过geocoding获取。"],
    city2:Annotated[ str , "终点所在的城市。注意，只接受城市编码(citycode)，例如，北京的话就是“010”。拿不准请通过geocoding获取。"],
    AlternativeRoute:Annotated[ int , "返回多少个路线规划结果，默认为2个"] = 2,
)-> dict:
    """
    公共交通路线规划工具：
    会返回地铁，公交，步行和少量出租车的组合路线行程规划，除去自驾和网约车的城市主要出行选择。
    注意：参数中的city1和city2只接收城市编码(citycode)。
    会返回多个规划结果。
    返回的结果第一层是每一个规划的路线。
        distance:本条路线的总距离（米）
        nightflag:是否夜班车(0：非夜班车；1：夜班车)

    每一条规划的路线分为多个segment。每一个segment里面可能会出现walking，bus，railway和taxi。
        其中walking又分为多个step，bus又分为多个busline。

    返回中：
        cost项里面的duration指时间成本，单位是以秒计算，transit_fee指换乘方案的总花费（元）
        start_time，station_start_time，end_time，station_end_time指首末班车发车时间（线路运行时间），0455表示早上4点55分。带station前缀表示首末班车实际到达该站点的时间。
        via_num：中间要经过几站
    """
    params = locals().copy()
    
    trimmed_result = {}
    params["show_fields"] = "cost"

    result = await api_request(AMAP_PUBLIC_TRANSIT_URL,params)
    if result["result_status"] == FAILURE_STR:
        return result
    if result["result_content"]["status"] == "0":
        return Api_result( FAILURE_STR , result["result_content"]["info"] ) 
    
    elif result["result_content"]["status"] == "1":
        trimmed_result["number of route scheduling results"] = result["result_content"]["count"]
        result_collect =  result["result_content"]["route"]["transits"]
        result_remove_keys = remove_specific_dict_key(
            original_data = result_collect,
            keys_to_remove = {"via_stops" ,  "id" , "location"}  )
        
        for i in range(len(result_remove_keys)): # convert list into dict
            key_here = f"route scheduling result no.{str(i)}"
            trimmed_result[key_here] = result_remove_keys[i]

        return trimmed_result
    
# #test
# cool_result  = asyncio.run(public_transit_route_planning(
#     origin = "116.466485,39.995197",
#     destination = "116.46424,40.020642" ,
#     city1="010",
#     city2="010" ,
#     AlternativeRoute = 1))
# print(cool_result)


######################################### bicycle_route_schedule #########################################
AMAP_BICYCLING_ROUTE_SCHEDULING_URL = os.getenv("AMAP_BICYCLING_ROUTE_SCHEDULING_URL")
@gaode_mcp.tool(tags = {"agent"})
async def bicycle_route_schedule(
    origin: Annotated[ str, "路线规划起点的经纬度，经度在前，纬度在后，经度和纬度用","分割，经纬度小数点后不得超过6位。例如：“116.482086,39.990496”" ],
    destination: Annotated[str , "路线规划终点的经纬度，格式要求与起点(origin)相同。"],
    alternative_route:Annotated[ int , "返回多少个路线规划结果，默认为1个，最多可以请求3个，不过api可以仍然只返回1个"] = 1,
)-> dict:
    """
    自行车骑行路线规划工具：
    可以返回多个规划结果。

    返回结果中：
        distance:本条路线的总距离（米）
        step_distance:这一个步骤的距离（米）
        duration指耗时多少秒
    """

    params = locals().copy()
    
    trimmed_result = {}
    params["show_fields"] = "cost"

    result = await api_request(AMAP_BICYCLING_ROUTE_SCHEDULING_URL,params)
    # print(result["result_content"]["status"])

    if result["result_status"] == FAILURE_STR:
        return result
    if result["result_content"]["status"] == "0":
        return Api_result( FAILURE_STR , result["result_content"]["info"] ) 
    
    elif result["result_content"]["status"] == "1":
        result_collect =  result["result_content"]["route"]
        trimmed_result["number of route scheduling results"] = result["result_content"]["count"]
        trimmed_result["route_results"] = result_collect
        return trimmed_result


# #test
# cool_result  = asyncio.run(bicycle_route_schedule(
#     origin = "116.466485,39.995197",
#     destination = "116.46424,40.020642" ,
#     alternative_route = 3,
#  ))
# print(cool_result)


from typing import Literal
######################################### automobile_driving_route_schedule #########################################
AMAP_AUTOMOBILE_DRIVING_URL = os.getenv("AMAP_AUTOMOBILE_DRIVING_URL")
@gaode_mcp.tool(tags = {"agent"})
async def automobile_driving_route_schedule(
    origin: Annotated[ str, "路线规划起点的经纬度，经度在前，纬度在后，经度和纬度用","分割，经纬度小数点后不得超过6位。例如：“116.482086,39.990496”" ],
    destination: Annotated[str , "路线规划终点的经纬度，格式要求与起点(origin)相同。"],
    strategy: Annotated[Literal[0,1,2] , "驾车算路策略：0：速度优先（只返回一条路线），此路线不一定距离最短；1：费用优先（只返回一条路线），不走收费路段，且耗时最少的路线；2：常规最快（只返回一条路线）综合距离/耗时规划结果"] = 2,
    destination_type: Annotated[ str , "终点的 poi 类别，当用户知道终点 POI 的类别时候，建议填充此值"] = None,
    origin_id:Annotated[ int , "起点 POI ID。起点为 POI 时，建议填充此值，可提升路线规划准确性"] = None,
    destination_id:Annotated[ int , "目的地 POI ID。目的地为 POI 时，建议填充此值，可提升路线规划准确性"] = None,
    waypoints:Annotated[ str , "途经点。途径点坐标串，默认支持1个有序途径点。多个途径点坐标按顺序以英文分号;分隔。最大支持16个途经点。"] = None,
    plate:Annotated[ str , "车牌号，如 京AHA322，支持6位传统车牌和7位新能源车牌，用于判断限行相关。"] = None,
)-> dict:
    """
    车辆驾驶路线规划工具：
    
    返回结果中：
        taxi_cost:如果是搭乘出租车的话，预计出租车的费用，单位：元
        distance:本路线方案的总距离（米）
        restriction:1表示该线路有限行路段，0表示没有
        step_distance:分段距离信息
        duration:线路耗时，分段 step 中的耗时，单位：秒
        tolls:此路线道路收费，单位：元，包括分段信息
        traffic_lights:方案中红绿灯个数，单位：个
    """
    params = locals().copy()
    params ["show_fields"] = "cost"

    result = await api_request(AMAP_AUTOMOBILE_DRIVING_URL,params)

    if result["result_status"] == FAILURE_STR:
        return result
    if result["result_content"]["status"] == "0":
        return Api_result( FAILURE_STR , result["result_content"]["info"] ) 
    
    elif result["result_content"]["status"] == "1":
        result_collect =  result["result_content"]["route"]
        return result_collect


# #test
# cool_result  = asyncio.run(automobile_driving_route_schedule(
#     origin = "116.466485,39.995197",
#     destination = "116.46424,40.020642" ,
#  ))
# print(cool_result)




######################################### keyword_search_of_poi #########################################
AMAP_KEYWORD_SEARCH_OF_POI_URL = os.getenv("AMAP_KEYWORD_SEARCH_OF_POI_URL")
@gaode_mcp.tool(tags = {"agent"})
async def keyword_search_of_poi(
    keywords: Annotated[ str, "需要被检索的POI名称（或者其他字段中）所应包含的关键词" ],
    region: Annotated[str , "在哪个区域内搜索，可输入 citycode，adcode，cityname；cityname 仅支持城市级别和中文，如“北京市”。默认全国范围内搜索"] = None,
    # types:Annotated[ str , """
    #                 所搜索的带有关键字的POI所具有的类型，可以传入多个 poi typecode，相互之间用“|”分隔。
    #                 typecode及其相应解释一览：
    #                 050000：餐饮服务(Food & Beverages)
    #                 060000：购物服务(Shopping)
    #                 080000：体育休闲服务(Sports & Recreation)
    #                 100000：住宿服务(Accommodation Service)
    #                 110000：风景名胜(Tourist Attraction)
    #                 140000：科教文化服务(Science/Culture & Education Service)
    #                 """] = "050000|060000",
    city_limit:Annotated[ str , "为 true 时，仅召回 region 对应区域内数据。"] = True,
    show_fields:Annotated[ str , "返回结果控制，多个字段间采用“,”进行分割"] = "business",
    page_size:Annotated[ int , "返回多少个结果，默认为5个，取值1-25"] = 5,
)-> dict:
    """
    以关键字形式搜索POI(Point of Interest)。
    返回值中的 POI id 和 POI type 可供automobile_driving_route_schedule使用。
    返回值中默认包含business信息，美食，玩乐，购物等等。
    """
    params = locals().copy()
    
    trimmed_result = {}

    result = await api_request(AMAP_KEYWORD_SEARCH_OF_POI_URL,params)
    if result["result_status"] == FAILURE_STR:
        return result
    if result["result_content"]["status"] == "0":
        return Api_result( FAILURE_STR , result["result_content"]["info"] ) 
    
    elif result["result_content"]["status"] == "1":
        trimmed_result["number of POI search results"] = result["result_content"]["count"]
        result_collect =  result["result_content"]["pois"]
        trimmed_result["search results"] = result_collect
        return trimmed_result
    
# #test
# cool_result  = asyncio.run(keyword_search_of_poi(
#     keywords = "欢乐谷",
#     region = "成都市",
# ))
# print(cool_result)


######################################### keyword_search_of_poi #########################################
AMAP_POI_AROUND_GIVEN_LOCATION_URL = os.getenv("AMAP_POI_AROUND_GIVEN_LOCATION_URL")
@gaode_mcp.tool(tags = {"agent"})
async def poi_around_given_location(
    location: Annotated[ str, "以经纬度给出的中心点坐标：圆形区域检索中心点，不支持多个点。经度和纬度用，（逗号）分割，经度在前，纬度在后，经纬度小数点后不得超过6位" ],
    radius: Annotated[int , "搜索半径，取值范围:0-50000，单位：米"] = 5000,
    keywords: Annotated[str , "打算寻找的POI所包含的关键字。只支持一个关键字 ，文本总长度不可超过80字符"] = None,
    sortrule:Annotated[ str , "排序规则，规定返回结果的排序规则。按距离排序：distance；综合排序：weight"] = "distance",
    types:Annotated[ str , """
                    指定地点类型，可以传入多个 poi typecode，相互之间用“|”分隔。
                    typecode及其相应解释一览：
                    050000：餐饮服务(Food & Beverages)
                    060000：购物服务(Shopping)
                    080000：体育休闲服务(Sports & Recreation)
                    100000：住宿服务(Accommodation Service)
                    110000：风景名胜(Tourist Attraction)
                    140000：科教文化服务(Science/Culture & Education Service)
                    """] = "050000|060000",
    show_fields:Annotated[ str , "返回结果控制，多个字段间采用“,”进行分割"] = "business",
    page_size:Annotated[ int , "返回多少个结果，默认为5个，取值1-25"] = 5,
)-> dict:
    """
    给出经纬度，搜索周围POI(Point of Interest)。
    有若干POI类型可以指定。
    返回值中的 POI id 和 POI type 可供automobile_driving_route_schedule使用。
    返回值中默认包含business信息，美食，玩乐，购物等等。
    """
    params = locals().copy()
    
    trimmed_result = {}

    result = await api_request(AMAP_POI_AROUND_GIVEN_LOCATION_URL,params)
    if result["result_status"] == FAILURE_STR:
        return result
    if result["result_content"]["status"] == "0":
        return Api_result( FAILURE_STR , result["result_content"]["info"] ) 
    
    elif result["result_content"]["status"] == "1":
        trimmed_result["number of POI search results"] = result["result_content"]["count"]
        result_collect =  result["result_content"]["pois"]
        trimmed_result["search results"] = result_collect
        return trimmed_result
    
# #test
# cool_result  = asyncio.run(poi_around_given_location(
#     location = "116.460988,40.006919",
#     keywords = "超市"
# ))
# print(cool_result)
if __name__ == "__main__":
    gaode_mcp.run()