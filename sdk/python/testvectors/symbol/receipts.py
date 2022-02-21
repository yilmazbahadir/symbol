SAMPLE_ADDRESS = 'TCIFSMQZAX3IDPHUP2RTXP26N6BJRNKEBBKP33I'


receipt_recipes_array = [
	{
		'schema_name': 'HarvestFeeReceipt',
		'descriptors': [
			{
				'type': 'harvest_fee_receipt',
				'mosaic': {'mosaic_id': 0x1234567890ABCDEF, 'amount': 0x0000000000000111},
				'target_address': SAMPLE_ADDRESS
			}
		]
	},
	{
		'schema_name': 'InflationReceipt',
		'descriptors': [
			{
				'type': 'inflation_receipt',
				'mosaic': {'mosaic_id': 0x234567890ABCDEF1, 'amount': 0x0000000000000222}
			}
		]
	},
	{
		'schema_name': 'LockHashCreatedFeeReceipt',
		'descriptors': [
			{
				'type': 'lock_hash_created_fee_receipt',
				'mosaic': {'mosaic_id': 0x34567890ABCDEF12, 'amount': 0x0000000000000333},
				'target_address': SAMPLE_ADDRESS
			}
		]
	},
	{
		'schema_name': 'LockHashCompletedFeeReceipt',
		'descriptors': [
			{
				'type': 'lock_hash_completed_fee_receipt',
				'mosaic': {'mosaic_id': 0x4567890ABCDEF123, 'amount': 0x0000000000000444},
				'target_address': SAMPLE_ADDRESS
			}
		]
	},
	{
		'schema_name': 'LockHashExpiredFeeReceipt',
		'descriptors': [
			{
				'type': 'lock_hash_expired_fee_receipt',
				'mosaic': {'mosaic_id': 0x567890ABCDEF1234, 'amount': 0x0000000000000555},
				'target_address': SAMPLE_ADDRESS
			}
		]
	},
]
