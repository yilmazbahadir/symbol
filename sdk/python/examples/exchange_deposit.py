import asyncio
import json
from collections import namedtuple

import sha3
from aiohttp import ClientSession
from zenlog import log

from symbolchain.CryptoTypes import Hash256, PrivateKey, PublicKey
from symbolchain.facade.SymbolFacade import SymbolFacade
from symbolchain.sc import TransactionType
from symbolchain.symbol.MerkleHashBuilder import MerkleHashBuilder
from symbolchain.symbol.NetworkTimestamp import NetworkTimestamp

FAUCET_ENDPOINT = 'https://testnet.symbol.tools'
NODE_ENDPOINT = 'http://sym-test-06.opening-line.jp:3000'
POLL_INTERVAL_SECONDS = 10
NUM_CONFIRMATIONS = 2  # this is only for example purposes, real number should be higher


class Logger:
	def __init__(self, tag):
		self.tag = tag
		self.indent = 0

	def log(self, message, indent=0):
		self.indent += indent

		if not self.indent:
			log.info(f'[{self.tag}] {message}')
		else:
			log.debug(f'{self.indent * "  "}[{self.tag}] {message}')

		self.indent -= indent


async def wait_for_confirmed_transaction(session, logger, transaction_hash, wait_seconds=POLL_INTERVAL_SECONDS):
	logger.indent += 1

	for _ in range(1, 10):
		async with session.get(f'{NODE_ENDPOINT}/transactionStatus/{transaction_hash}') as response:
			response_json = await response.json()
			status_code = response_json['code']
			if 'Success' == status_code:
				logger.log('transaction has been confirmed')
				break

			logger.log(f'transaction has been not confirmed, current status is {status_code}, waiting {wait_seconds}s')
			await asyncio.sleep(wait_seconds)

	if 'Success' != status_code:
		raise RuntimeError(f'timed out waiting for confirmation of {transaction_hash} transaction')

	logger.indent -= 1


async def seed_account_from_faucet(session, address, mosaic_id, amount):
	request_params = {
		'amount': amount,
		'recipient': str(address),
		'selectedMosaics': [f'{mosaic_id:016X}']
	}

	async with session.post(f'{FAUCET_ENDPOINT}/claims', json=request_params) as response:
		response_json = await response.json()
		if 'txHash' not in response_json:
			print(response_json)

		return response_json['txHash']


async def get_network_time(session):
	async with session.get(f'{NODE_ENDPOINT}/node/time') as response:
		response_json = await response.json()
		return NetworkTimestamp(int(response_json['communicationTimestamps']['sendTimestamp']))


def wrap_none(facade, descriptor):
	return facade.transaction_factory.create(descriptor)


def wrap_aggregate(facade, descriptor):
	embedded_transactions = [
		facade.transaction_factory.create_embedded({
			'type': 'transfer_transaction',
			'signer_public_key': descriptor['signer_public_key'],
			'recipient_address': descriptor['recipient_address'],
			'mosaics': descriptor['mosaics']
		})
	]

	hash_builder = MerkleHashBuilder()
	for embedded_transaction in embedded_transactions:
		hash_builder.update(Hash256(sha3.sha3_256(embedded_transaction.serialize()).digest()))

	merkle_hash = hash_builder.final()

	return facade.transaction_factory.create({
		'type': 'aggregate_complete_transaction',
		'signer_public_key': descriptor['signer_public_key'],
		'deadline': descriptor['deadline'],
		'transactions_hash': merkle_hash,
		'transactions': embedded_transactions
	})


async def seed_client(facade, mosaic_id, amount):
	logger = Logger('faucet')

	key_pair = facade.KeyPair(PrivateKey.random())
	address = facade.network.public_key_to_address(key_pair.public_key)
	logger.log(f'created client with address {address}')
	logger.indent += 1

	async with ClientSession() as session:
		# seed account
		seed_transaction_hash = await seed_account_from_faucet(session, address, mosaic_id, amount)
		logger.log(f'faucet is funding {address} with transaction {seed_transaction_hash}')

		await wait_for_confirmed_transaction(session, logger, seed_transaction_hash)

	return key_pair


async def simulate_client(facade, client_key_pair, recipient_address, mosaic_id, amounts, wrapper):
	# pylint: disable=too-many-arguments, too-many-locals
	logger = Logger('client')

	async with ClientSession() as session:
		# get the current network time and set the deadline one hour in the future
		current_timestamp = await get_network_time(session)
		deadline = current_timestamp.add_hours(1)
		logger.log(f'retrieved network time {current_timestamp}')

		# prepare a simple transfer
		transaction_hashes = []
		for amount in amounts:
			transaction = wrapper(facade, {
				'type': 'transfer_transaction',
				'signer_public_key': client_key_pair.public_key,
				'deadline': deadline.timestamp + len(transaction_hashes),
				'recipient_address': recipient_address,
				'mosaics': [
					{'mosaic_id': mosaic_id, 'amount': amount}
				]
			})

			transaction.fee.value = transaction.size * 100

			signature = facade.sign_transaction(client_key_pair, transaction)
			transaction_json = json.loads(facade.transaction_factory.attach_signature(transaction, signature))

			transaction_hashes.append(facade.hash_transaction(transaction))
			logger.log(f'pushing transaction with hash {transaction_hashes[-1]}')

			async with session.put(f'{NODE_ENDPOINT}/transactions', json=transaction_json) as response:
				logger.log('pushed transaction to network', 1)
				await response.json()

		for transaction_hash in transaction_hashes:
			await wait_for_confirmed_transaction(session, logger, transaction_hash)


async def get_chain_heights(session):
	async with session.get(f'{NODE_ENDPOINT}/chain/info') as response:
		response_json = await response.json()
		height = int(response_json['height'])
		finalized_height = int(response_json['latestFinalizedBlock']['height'])
		return namedtuple('ChainHeights', ['confirmed', 'finalized'])(height, finalized_height)


class AccountRepository:
	def __init__(self, mosaic_id_friendly_name_map):
		self.mosaic_id_friendly_name_map = mosaic_id_friendly_name_map
		self.map = {}
		self.num_deposits = 0
		self.max_processed_height = None

	def credit(self, signer_address, mosaic_id, amount):
		friendly_name = self.mosaic_id_friendly_name_map.get(mosaic_id)
		if not friendly_name:
			return

		if signer_address not in self.map:
			self.map[signer_address] = {}

		if friendly_name not in self.map[signer_address]:
			self.map[signer_address][friendly_name] = 0

		self.map[signer_address][friendly_name] += amount
		self.num_deposits += 1


def process_transfer(facade, account_repository, transaction_json):
	transaction_type = TransactionType(transaction_json['type'])
	if TransactionType.TRANSFER == transaction_type:
		signer_address = facade.network.public_key_to_address(PublicKey(transaction_json['signerPublicKey']))

		for mosaic_json in transaction_json['mosaics']:
			account_repository.credit(signer_address, int(mosaic_json['id'], 16), int(mosaic_json['amount']))
	else:
		log.warn(f'ignoring {transaction_type} transaction')


async def process_aggregate(session, facade, account_repository, transaction_hash):
	async with session.get(f'{NODE_ENDPOINT}/transactions/confirmed/{transaction_hash}') as response:
		response_json = await response.json()
		for transaction_meta_json in response_json['transaction']['transactions']:
			process_transfer(facade, account_repository, transaction_meta_json['transaction'])


async def simulate_exchange_deposit(facade, account_repository, exchange_address):
	# pylint: disable=too-many-locals
	logger = Logger('server')

	start_id = None
	min_processed_height = account_repository.max_processed_height
	max_processed_height = None

	logger.log(f'processing transactions after {min_processed_height} ({account_repository.num_deposits} processed)')

	async with ClientSession() as session:
		heights = await get_chain_heights(session)

		while True:
			query_params = f'address={exchange_address}&order=desc&pageSize=10'
			if start_id:
				query_params += f'&offset={start_id}'

			async with session.get(f'{NODE_ENDPOINT}/transactions/confirmed?{query_params}') as response:
				response_json = await response.json()
				for transaction_meta_json in response_json['data']:
					meta_json = transaction_meta_json['meta']
					transaction_height = int(meta_json['height'])

					# wait for some confirmations; alternatively can wait for finalized height
					num_confirmations = heights.confirmed - transaction_height
					if num_confirmations < NUM_CONFIRMATIONS:
						logger.log(f'skipping transaction with {num_confirmations} confirmations', 1)
						continue

					# avoid reprocessing already processed heights
					if min_processed_height and transaction_height <= min_processed_height:
						account_repository.max_processed_height = max_processed_height if max_processed_height else min_processed_height
						logger.log(f'skipping older transaction with height {transaction_height}', 1)
						return

					if not max_processed_height:
						max_processed_height = transaction_height

					finalization_qualifier = '' if heights.finalized >= transaction_height else 'NOT '
					logger.log(
						f'processing transaction {meta_json["hash"]} with {num_confirmations} confirmations '
						f'({finalization_qualifier}finalized) at {transaction_height}')

					transaction_json = transaction_meta_json['transaction']
					if 'cosignatures' in transaction_json:
						await process_aggregate(session, facade, account_repository, meta_json['hash'])
					else:
						process_transfer(facade, account_repository, transaction_json)

				if not response_json['data']:
					break

				start_id = response_json['data'][-1]['id']

	account_repository.max_processed_height = max_processed_height


def validate_deposits(account_repository, expected_balance_pairs):
	# print contents of repository
	log.info('validating deposits...')

	for address, mosaics in account_repository.map.items():
		log.info(address)
		for friendly_name, amount in mosaics.items():
			log.debug(f'  {friendly_name}: {amount}')

	# validate contents
	assert 2 == len(expected_balance_pairs)
	assert 2 == len(account_repository.map)

	for balance_pair in expected_balance_pairs:
		address = balance_pair[0]
		assert address in account_repository.map
		assert 1 == len(account_repository.map[address])
		assert 'symbol.xym' in account_repository.map[address]
		assert balance_pair[1] == account_repository.map[address]['symbol.xym']


async def main():
	# pylint: disable=too-many-locals

	# create key pair for exchange account
	facade = SymbolFacade('testnet')
	exchange_key_pair = facade.KeyPair(PrivateKey.random())
	exchange_address = facade.network.public_key_to_address(exchange_key_pair.public_key)
	log.info(f'created exchange address: {exchange_address}')

	# set up mapping between mosaic and friendly name
	mosaic_id_friendly_name_map = {}
	mosaic_id_friendly_name_map[0x3A8416DB2D53B6C8] = 'symbol.xym'
	mosaic_id_friendly_name_map[0xE74B99BA41F4AFEE] = 'symbol.xym'  # namespace alias

	# seed client accounts (faucet does not support alias)
	client_key_pair1 = await seed_client(facade, 0x3A8416DB2D53B6C8, 1)
	client_key_pair2 = await seed_client(facade, 0x3A8416DB2D53B6C8, 1)

	# send transactions from two accounts using regular, aggregates, etc
	multiplier = 1
	amounts = [1, 2, 4]
	num_expected_deposits = len(amounts) * len(mosaic_id_friendly_name_map) * 2 * 2

	for mosaic_id, _ in mosaic_id_friendly_name_map.items():
		for client_key_pair in (client_key_pair1, client_key_pair2):
			for wrapper in (wrap_none, wrap_aggregate):
				adjusted_amounts = [amount * multiplier for amount in amounts]

				client_id = 1 if client_key_pair1 == client_key_pair else 2
				wrapper_id = 'basic' if wrap_none == wrapper else 'aggregate'
				log.info(f'processing client{client_id} with {wrapper_id} for total of {sum(adjusted_amounts)} mosaic {mosaic_id:016X}')

				await simulate_client(facade, client_key_pair, exchange_address, mosaic_id, adjusted_amounts, wrapper)
				multiplier += 1

	# simulate exchange behvior
	log.info(f'collecting deposits ({num_expected_deposits} expected)')

	account_repository = AccountRepository(mosaic_id_friendly_name_map)
	for _ in range(1, 10):
		await simulate_exchange_deposit(facade, account_repository, exchange_address)
		if num_expected_deposits == account_repository.num_deposits:
			break

		await asyncio.sleep(2 * POLL_INTERVAL_SECONDS)

	if num_expected_deposits != account_repository.num_deposits:
		raise RuntimeError('timed out waiting for deposits')

	validate_deposits(account_repository, [
		(facade.network.public_key_to_address(client_key_pair1.public_key), 98),
		(facade.network.public_key_to_address(client_key_pair2.public_key), 154)
	])


asyncio.run(main())
