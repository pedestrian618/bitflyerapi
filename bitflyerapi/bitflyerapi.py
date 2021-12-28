# -*- coding: utf-8 -*-

import hmac
import hashlib
import json
import time
import urllib

import requests

from . import AuthException

class BitflyerAPI(object):
	def __init__(self, *args, **config):
		self.key = config["key"]
		self.secret	= config["secret"]
		self.timeout = (float(config["connect_timeout"]),
						float(config["read_timeout"]))
		self.top = 'https://api.bitflyer.com'
		self.public = '/v1/'
		self.private = '/v1/me/'
		self.header = None

	def _make_header(self, path, method, params):
		"""
		ACCESS-KEY: API key issued by the developer's page
		ACCESS-TIMESTAMP: The request's Unix Timestamp.
		ACCESS-SIGN: Signature generated for each request with the following method.
		The ACCESS-SIGN is the resulting HMAC-SHA256 hash of the ACCESS-TIMESTAMP, 
		HTTP method, request path, and request body concatenated together as a character
		string, created using your API secret.
		"""
		timestamp = str(time.time())
		body = ""
		if method == "POST":
			body = json.dumps(params)
		else:
			if params:
				body = "?" + urllib.parse.urlencode(params)
		text = timestamp + method + path + body
		signature = hmac.new(str.encode(self.secret),
							str.encode(text),
							hashlib.sha256).hexdigest()

		header = {
			"ACCESS-KEY": self.key,
			"ACCESS-TIMESTAMP": timestamp,
			"ACCESS-SIGN": signature,
			"Content-Type": "application/json"
		}
		self.header = header

	def request(self,path,method='GET',params=None):
		url = self.top + path
		try:
			with requests.Session() as s:
				if self.key and self.secret:
					self._make_header(path,method,params)
					s.headers.update(self.header)
				if method == 'GET':
					response = s.get(url,params=params,timeout=self.timeout)
				else:
					response = s.post(url,data = json.dumps(params),
										timeout=self.timeout)
		except Exception as e:
			print(e)
			raise e

		content = json.loads(response.content.decode("utf-8"))
		return(content)

	"""HTTP PUBLIC API"""

	def markets(self,region = None):
		"""region: None(JPY), "usa" or "eu" """
		path = self.public + 'markets'
		path += f'/{region}' if region else ''
		return self.request(path)

	def getmarkets(self,region = None):
		"""region: None(JPY), "usa" or "eu" """
		path = self.public + 'getmarkets'
		path += f'/{region}' if region else ''
		return self.request(path)

	def board(self,**params):
		"""
		Query parameters

		product_code: Please specify a product_code or alias, 
		as obtained from the Market List (markets or getmarkets).
		Please refer to the 
		Regions to check available products in each region.
		(ex:'BTC_JPY','XRP_JPY','ETH_JPY'...)
		"""
		path = self.public + 'board'
		return self.request(path,params = params)

	def getboard(self,**params):
		"""
		Query parameters

		product_code: Please specify a product_code or alias, 
		as obtained from the Market List (markets or getmarkets).
		Please refer to the 
		Regions to check available products in each region.
		(ex:'BTC_JPY','XRP_JPY','ETH_JPY'...)
		"""
		path = self.public + 'getboard'
		return self.request(path,params = params)

	def ticker(self,**params):
		"""
		Query parameters

		product_code: Please specify a product_code or alias, 
		as obtained from the Market List (markets or getmarkets).
		Please refer to the 
		Regions to check available products in each region.
		(ex:'BTC_JPY','XRP_JPY','ETH_JPY'...)
		"""
		path = self.public + 'ticker'
		return self.request(path,params = params)

	def getticker(self,**params):
		"""
		Query parameters

		product_code: Please specify a product_code or alias, 
		as obtained from the Market List (markets or getmarkets).
		Please refer to the 
		Regions to check available products in each region.
		(ex:'BTC_JPY','XRP_JPY','ETH_JPY'...)
		"""
		path = self.public + 'getticker'
		return self.request(path,params = params)

	def getexecutions(self,**params):
		"""
		Query parameters

		product_code: Please specify a product_code or alias, 
		as obtained from the Market List (markets or getmarkets).
		Please refer to the 
		Regions to check available products in each region.

		count, before, after: See Pagination. As of December 19, 
		2018, the execution history obtainable through the before 
		parameter will be limited to the most recent 31 days.

		count: Specifies the number of results. If this is omitted, 
				the value will be 100.
		before: Obtains data having an id lower than the value 
				specified for this parameter.
		after: Obtains data having an id higher than the value 
				specified for this parameter.
		"""
		path = self.public + 'getexecutions'
		return self.request(path,params = params)

	def executions(self,**params):
		"""
		Query parameters

		product_code: Please specify a product_code or alias, 
		as obtained from the Market List (markets or getmarkets).
		Please refer to the 
		Regions to check available products in each region.

		count, before, after: See Pagination. As of December 19, 
		2018, the execution history obtainable through the before 
		parameter will be limited to the most recent 31 days.

		count: Specifies the number of results. If this is omitted, 
				the value will be 100.
		before: Obtains data having an id lower than the value 
				specified for this parameter.
		after: Obtains data having an id higher than the value 
				specified for this parameter.
		"""
		path = self.public + 'executions'
		return self.request(path,params = params)

	def getboardstate(self,**params):
		"""
		Query parameters

		product_code: Please specify a product_code or alias, 
		as obtained from the Market List (markets or getmarkets).
		Please refer to the 
		Regions to check available products in each region.
		(ex:'BTC_JPY','XRP_JPY','ETH_JPY'...)
		"""
		path = self.public + 'getboardstate'
		return self.request(path,params = params)

	def gethealth(self,**params):
		"""
		Query parameters

		product_code: Please specify a product_code or alias, 
		as obtained from the Market List (markets or getmarkets).
		Please refer to the 
		Regions to check available products in each region.
		(ex:'BTC_JPY','XRP_JPY','ETH_JPY'...)
		"""
		path = self.public + 'gethealth'
		return self.request(path,params = params)

	def getcorporateleverage(self,**params):
		path = self.public + 'getcorporateleverage'
		return self.request(path)

	def getchats(self,region = None, **params):
		"""
		region: None(JPY), "usa" or "eu" 

		Query parameters

		from_date: This accesses a list of any new messages after 
		this date. Defaults to the previous 5 days if left blank.
		(ex:from_date="2020-12-21")
		"""
		path = self.public + 'getchats'
		path += f'/{region}' if region else ''
		return self.request(path,params = params)


	"""HTTP PRIVATE API"""
	def auth_execution(func):
		def inner(self,*args,**params):
			if not all([self.key, self.secret]):
				raise AuthException()
			return func(self, *args, **params)
		return inner

	@auth_execution
	def getpermissions(self,**params):
		path = self.private + 'getpermissions'
		return self.request(path)

	@auth_execution
	def getbalance(self,**params):
		path = self.private + 'getbalance'
		return self.request(path)

	@auth_execution
	def getcollateral(self,**params):
		path = self.private + 'getcollateral'
		return self.request(path)

	@auth_execution
	def getcollateralaccounts(self,**params):
		path = self.private + 'getcollateralaccounts'
		return self.request(path)

	@auth_execution
	def getaddresses(self,**params):
		path = self.private + 'getaddresses'
		return self.request(path)

	@auth_execution
	def getcoinins(self,**params):
		"""
		Query parameters
		count: Specifies the number of results. If this is omitted, 
				the value will be 100.
		before: Obtains data having an id lower than the value 
				specified for this parameter.
		after: Obtains data having an id higher than the value 
				specified for this parameter.
		"""
		path = self.private + 'getcoinins'
		return self.request(path, params = params)

	@auth_execution
	def getcoinouts(self, **params):
		"""
		Query parameters
		count: Specifies the number of results. If this is omitted, 
				the value will be 100.
		before: Obtains data having an id lower than the value 
				specified for this parameter.
		after: Obtains data having an id higher than the value 
				specified for this parameter.
		"""
		path = self.private + 'getcoinouts'
		return self.request(path, params = params)

	@auth_execution
	def getbankaccounts(self, **params):
		path = self.private + 'getbankaccounts'
		return self.request(path, params = params)

	@auth_execution
	def getdeposits(self, **params):
		"""
		Query parameters
		count: Specifies the number of results. If this is omitted, 
				the value will be 100.
		before: Obtains data having an id lower than the value 
				specified for this parameter.
		after: Obtains data having an id higher than the value 
				specified for this parameter.
		"""
		path = self.private + 'getdeposits'
		return self.request(path, params = params)

	@auth_execution
	def withdraw(self, **params):
		"""
		body params
		{
		"currency_code": "JPY",
		"bank_account_id": 1234,
		"amount": 12000
		"code": "012345"
		}
		currency_code: Required. Currently only compatible with "JPY" for Japanese accounts.
		bank_account_id: Required. The id of the bank account.
		amount: Required. The amount that you are canceling.
		code: Two-factor authentication code. Reference the two-factor authentication section.
		Additional fees apply for withdrawals. Please see the Fees and Taxes page for reference.
		"""
		path = self.private + 'withdraw'
		return self.request(path, params = params)

	@auth_execution
	def getwithdrawals(self, **params):
		"""
		Query parameters
		count: Specifies the number of results. If this is omitted, 
				the value will be 100.
		before: Obtains data having an id lower than the value 
				specified for this parameter.
		after: Check the withdrawal status by specifying the receipt 
				ID from the returned value from the withdrawal API.
		"""
		path = self.private + 'getwithdrawals'
		return self.request(path, params = params)

	@auth_execution
	def sendchildorder(self, **params):
		"""
		Body parameters

		{
		  "product_code": "BTC_JPY",
		  "child_order_type": "LIMIT",
		  "side": "BUY",
		  "price": 30000,
		  "size": 0.1,
		  "minute_to_expire": 10000,
		  "time_in_force": "GTC"
		}
		product_code: Please specify a product_code or alias, as obtained from the Market List. 
		Please refer to the Regions to check available products in each region.

		child_order_type: Required. For limit orders, it will be "LIMIT". For market orders, "MARKET".

		side: Required. For buy orders, "BUY". For sell orders, "SELL".

		price: Specify the price. This is a required value if child_order_type has been set to "LIMIT".

		size: Required. Specify the order quantity.

		minute_to_expire: Specify the time in minutes until the expiration time. If omitted, 
		the value will be 43200 (30 days).

		time_in_force: Specify any of the following execution conditions - "GTC", "IOC", or "FOK". 
		If omitted, the value defaults to "GTC".
		"""
		path = self.private + 'sendchildorder'
		return self.request(path, "POST",params = params)

	@auth_execution
	def cancelchildorder(self, **params):
		"""
		Body parameters
		{
		  "product_code": "BTC_JPY",
		  "child_order_id": "JOR20150707-055555-022222"
		}

		{
		  "product_code": "BTC_JPY",
		  "child_order_acceptance_id": "JRF20150707-033333-099999"
		}
		product_code: Please specify a product_code or alias, as obtained from the Market List. 
		Please refer to the Regions to check available products in each region.
		Please specify either child_order_id or child_order_acceptance_id

		child_order_id: ID for the canceling order.

		child_order_acceptance_id: Expects an ID from Send a New Order. When specified, the 
		corresponding order will be cancelled.
		"""
		path = self.private + 'cancelchildorder'
		return self.request(path, "POST",params = params)

	@auth_execution
	def sendparentorder(self, **params):
		"""
		Body parameters

		{
		  "order_method": "IFDOCO",
		  "minute_to_expire": 10000,
		  "time_in_force": "GTC",
		  "parameters": [{
		    "product_code": "BTC_JPY",
		    "condition_type": "LIMIT",
		    "side": "BUY",
		    "price": 30000,
		    "size": 0.1
		  },
		  {
		    "product_code": "BTC_JPY",
		    "condition_type": "LIMIT",
		    "side": "SELL",
		    "price": 32000,
		    "size": 0.1
		  },
		  {
		    "product_code": "BTC_JPY",
		    "condition_type": "STOP_LIMIT",
		    "side": "SELL",
		    "price": 28800,
		    "trigger_price": 29000,
		    "size": 0.1
		  }]
		}
		order_method: The order method. Please set it to one of the following values. If omitted, 
		the value defaults to "SIMPLE".
		"SIMPLE": A special order whereby one order is placed.
		"IFD": Conducts an IFD order. In this method, you place two orders at once, and when the 
		first order is completed, the second order is automatically placed.

		"OCO": Conducts an OCO order. In this method, you place two orders at one, and when one 
		of the orders is completed, the other order is automatically canceled.

		"IFDOCO": Conducts an IFD-OCO order. In this method, once the first order is completed, an 
		OCO order is automatically placed.

		minute_to_expire: Specifies the time until the order expires in minutes. If omitted, the 
		value defaults to 43200 (30 days).
		time_in_force: Specify any of the following execution conditions - "GTC", "IOC", or "FOK". 
		If omitted, the value defaults to "GTC".
		parameters: Required value. This is an array that specifies the parameters of the order to 
		be placed. The required length of the array varies depending upon the specified order_method.
		If "SIMPLE" has been specified, specify one parameter.
		If "IFD" has been specified, specify two parameters. The first parameter is the parameter for 
		the first order placed. The second parameter is the parameter for the order to be placed after 
		the first order is completed.
		If "OCO" has been specified, specify two parameters. Two orders are placed simultaneously 
		based on these parameters.
		If "IFDOCO" has been specified, specify three parameters. The first parameter is the parameter 
		for the first order placed. After the order is complete, an OCO order is placed with the second 
		and third parameters.
		In the parameters, specify an array of objects with the following keys and values.

		product_code: Required value. This is the product to be ordered. Please specify a product_code 
		or alias, as obtained from the Market List. Please refer to the Regions to check available products 
		in each region.

		condition_type: Required value. This is the execution condition for the order. Please set it to 
		one of the following values.
		"LIMIT": Limit order.
		"MARKET": Market order.
		"STOP": Stop order.
		"STOP_LIMIT": Stop-limit order.
		"TRAIL": Trailing stop order.
		side: Required value. For buying orders, specify "BUY", for selling orders, specify "SELL".
		size: Required value. Specify the order quantity.
		price: Specify the price. This is a required value if condition_type has been set to "LIMIT" or 
		"STOP_LIMIT".
		trigger_price: Specify the trigger price for a stop order. This is a required value if condition_type 
		has been set to "STOP" or "STOP_LIMIT".
		offset: Specify the trail width of a trailing stop order as a positive integer. This is a required 
		value if condition_type has been set to "TRAIL".
		"""
		path = self.private + 'sendparentorder'
		return self.request(path, "POST",params = params)

	@auth_execution
	def cancelparentorder(self, **params):
		"""
		Body parameters

		{
		  "product_code": "BTC_JPY",
		  "parent_order_id": "JCO20150925-055555-022222"
		}

		{
		  "product_code": "BTC_JPY",
		  "parent_order_acceptance_id": "JRF20150925-033333-099999"
		}
		product_code: Required. The product for the corresponding order. Please specify 
		a product_code or alias, as obtained from the Market List. Please refer to the 
		Regions to check available products in each region.
		Please specify only one between parent_order_id and parent_order_acceptance_id

		parent_order_id: ID for the canceling order.
		parent_order_acceptance_id: Expects an ID from Submit New Parent Order. When 
		specified, the corresponding order will be cancelled.

		"""
		path = self.private + 'cancelparentorder'
		return self.request(path, "POST",params = params)

	@auth_execution
	def cancelallchildorders(self, **params):
		"""
		Body parameters

		{
		  "product_code": "BTC_JPY"
		}
		product_code: The product for the corresponding order. Please specify a product_code 
		or alias, as obtained from the Market List. Please refer to the Regions to check 
		available products in each region.

		"""
		path = self.private + 'cancelallchildorders'
		return self.request(path, "POST",params = params)

	@auth_execution
	def getchildorders(self, **params):
		"""
		Query parameters

		product_code: Please specify a product_code or alias, as obtained from the Market List. 
		Please refer to the Regions to check available products in each region.

		count, before, after: See Pagination.


		child_order_state: When specified, return only orders that match the specified value. 
		You must specify one of the following:
		ACTIVE: Return open orders
		COMPLETED: Return fully completed orders
		CANCELED: Return orders that have been cancelled by the customer
		EXPIRED: Return order that have been cancelled due to expiry
		REJECTED: Return failed orders


		child_order_id, child_order_acceptance_id: ID for the child order.
		parent_order_id: If specified, a list of all orders associated with the parent order is obtained.
		"""
		path = self.private + 'getchildorders'
		return self.request(path, params = params)

	@auth_execution
	def getparentorders(self, **params):
		"""
		Query parameters

		product_code: Please specify a product_code or alias, as obtained from the Market List. 
		Please refer to the Regions to check available products in each region.

		count, before, after: See Pagination.


		child_order_state: When specified, return only orders that match the specified value. 
		You must specify one of the following:
		ACTIVE: Return open orders
		COMPLETED: Return fully completed orders
		CANCELED: Return orders that have been cancelled by the customer
		EXPIRED: Return order that have been cancelled due to expiry
		REJECTED: Return failed orders
		"""
		path = self.private + 'getparentorders'
		return self.request(path, params = params)

	@auth_execution
	def getparentorder(self, **params):
		"""
		Query parameters

		Please specify either parent_order_id or parent_order_acceptance_id.

		parent_order_id: The ID of the parent order in question.
		parent_order_acceptance_id: The acceptance ID for the API to place a new parent order. 
		If specified, it returns the details of the parent order in question.
		"""
		path = self.private + 'getparentorder'
		return self.request(path, params = params)

	@auth_execution
	def getexecutions(self, **params):
		"""
		Query parameters

		product_code: Please specify a product_code or alias, as obtained from the Market List. 
		Please refer to the Regions to check available products in each region.

		count, before, after: See Pagination.

		child_order_id: Optional. When specified, a list of stipulations related to the order 
		will be displayed.

		child_order_acceptance_id: Optional. Expects an ID from Send a New Order. When specified, 
		a list of stipulations related to the corresponding order will be displayed.
		"""
		path = self.private + 'getexecutions'
		return self.request(path, params = params)

	@auth_execution
	def getbalancehistory(self, **params):
		"""
		Query parameters

		currency_code: Please specify a currency code. If omitted, the value is set to JPY.
		count, before, after: See Pagination.
		"""
		path = self.private + 'getbalancehistory'
		return self.request(path, params = params)

	@auth_execution
	def getpositions(self, **params):
		"""
		Query parameters

		product_code: Currently supports Lightning FX and Lightning Futures.
		"""
		path = self.private + 'getpositions'
		return self.request(path, params = params)


	@auth_execution
	def getcollateralhistory(self, **params):
		"""
		Query parameters

		count, before, after: See Pagination.
		"""
		path = self.private + 'getcollateralhistory'
		return self.request(path, params = params)

	@auth_execution
	def gettradingcommission(self, **params):
		"""
		Query parameters

		product_code: Please specify a product_code or alias, as obtained from the Market List. 
		Please refer to the Regions to check available products in each region.
		"""
		path = self.private + 'gettradingcommission'
		return self.request(path, params = params)








