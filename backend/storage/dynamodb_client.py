"""
Async DynamoDB client for metadata storage operations.

This module provides async wrappers around boto3 DynamoDB operations using
asyncio.to_thread() to run blocking boto3 calls in thread pool.

Single-table design:
    Table name: DYNAMODB_TABLE_NAME (e.g., "compliance-platform")
    PK: Partition key (WORKSPACE#{id}, USER#{id}, etc.)
    SK: Sort key (META, MEMBER#{id}, CASE#{id}, AUDIT#{timestamp}, JOB#{id}, etc.)
"""
import asyncio
import logging
from typing import Optional
from botocore.exceptions import ClientError

from ..config import settings

logger = logging.getLogger(__name__)


def _map_boto3_exception_to_http_status(exc: Exception) -> tuple[int, str]:
    """Map boto3 exceptions to HTTP status codes and readable messages.
    
    Maps DynamoDB-specific exceptions to appropriate HTTP status codes:
    - ResourceNotFoundException → 404 (item/table not found)
    - ValidationException → 400 (invalid request)
    - ConditionalCheckFailedException → 409 (conflict)
    - ProvisionedThroughputExceededException → 503 (service unavailable)
    - ThrottlingException → 503 (throttled)
    - InternalServerError → 503 (AWS internal error)
    - ServiceUnavailableException → 503 (service unavailable)
    - Generic Exception → 500 (internal error)
    
    Args:
        exc: Exception from boto3 or other source
    
    Returns:
        Tuple of (http_status_code, user_friendly_message)
    """
    if isinstance(exc, ClientError):
        error_code = exc.response.get('Error', {}).get('Code', 'Unknown')
        
        if error_code == 'ResourceNotFoundException':
            return 404, "Resource not found"
        elif error_code == 'ValidationException':
            return 400, "Invalid request parameters"
        elif error_code == 'ConditionalCheckFailedException':
            return 409, "Conditional update failed (item may have been modified)"
        elif error_code == 'ProvisionedThroughputExceededException':
            return 503, "Service temporarily unavailable (throughput exceeded)"
        elif error_code == 'ThrottlingException':
            return 503, "Service temporarily unavailable (too many requests)"
        elif error_code in ('InternalServerError', 'ServiceUnavailableException'):
            return 503, "Service temporarily unavailable"
        else:
            # For other ClientErrors, default to 500
            return 500, f"Database operation failed: {error_code}"
    
    # For non-ClientError exceptions, default to 500
    return 500, "An unexpected error occurred"


def _get_error_message(exc: Exception) -> str:
    """Extract readable error message from boto3 exception.
    
    Extracts the AWS error message while avoiding exposing internal
    DynamoDB implementation details.
    
    Args:
        exc: Exception from boto3 or other source
    
    Returns:
        User-friendly error message string
    """
    if isinstance(exc, ClientError):
        error_code = exc.response.get('Error', {}).get('Code', 'Unknown')
        error_msg = exc.response.get('Error', {}).get('Message', str(exc))
        
        # Map common DynamoDB error codes to user-friendly messages
        if error_code == 'ResourceNotFoundException':
            return "The requested resource was not found"
        elif error_code == 'ValidationException':
            return f"Invalid request: {error_msg[:100]}"  # Truncate to avoid exposing internals
        elif error_code == 'ConditionalCheckFailedException':
            return "Update condition failed - the item may have been modified by another request"
        elif error_code in ('ProvisionedThroughputExceededException', 'ThrottlingException'):
            return "Service is temporarily overloaded. Please try again in a moment."
        else:
            return error_msg[:200]  # Truncate long AWS messages
    
    return str(exc)[:200]


def _log_error(operation: str, pk: str, sk: str, exc: Exception) -> None:
    """Log error with full context including AWS error code.
    
    Logs errors at ERROR level with:
    - Operation type being performed
    - Partition key and sort key (for context, not sensitive)
    - AWS error code if available
    - AWS request ID if available
    - Full stack trace
    
    Args:
        operation: Name of the operation (e.g., 'get_item', 'put_item')
        pk: Partition key value (safe to log, used for context)
        sk: Sort key value (safe to log, used for context)
        exc: Exception that occurred
    """
    if isinstance(exc, ClientError):
        error_code = exc.response.get('Error', {}).get('Code', 'Unknown')
        request_id = exc.response.get('ResponseMetadata', {}).get('RequestId', 'N/A')
        
        logger.error(
            f"DynamoDB {operation} failed | "
            f"PK={pk} | SK={sk} | "
            f"ErrorCode={error_code} | "
            f"RequestID={request_id}",
            exc_info=True
        )
    else:
        logger.error(
            f"DynamoDB {operation} failed | PK={pk} | SK={sk} | {type(exc).__name__}",
            exc_info=True
        )


class DynamoDBClient:
    """Async DynamoDB client for workspace metadata operations.
    
    All operations use asyncio.to_thread() to run boto3 calls without blocking
    the event loop. This enables true async/await semantics for FastAPI.
    """
    
    def __init__(self):
        """Initialize async boto3 DynamoDB client.
        
        Raises:
            RuntimeError: If boto3 is not installed
            RuntimeError: If table name is not configured
        """
        try:
            import boto3
        except ImportError as exc:
            raise RuntimeError(
                "boto3 is not installed; install project requirements before enabling DynamoDB"
            ) from exc
        
        if not settings.DYNAMODB_TABLE_NAME:
            raise RuntimeError("DYNAMODB_TABLE_NAME is not configured in settings")
        
        # Initialize boto3 resource (we'll use client for direct operations)
        self.boto3 = boto3
        self._table = None
        self._client = None
        self._dynamodb = None
        logger.info(f"✓ DynamoDBClient initialized for table: {settings.DYNAMODB_TABLE_NAME}")
    
    def _map_boto3_exception_to_http_status(self, exc: Exception) -> tuple[int, str]:
        """Map boto3 exceptions to HTTP status codes.
        
        Returns:
            Tuple of (http_status_code, user_friendly_message)
        """
        return _map_boto3_exception_to_http_status(exc)
    
    def _get_error_message(self, exc: Exception) -> str:
        """Extract readable error message from boto3 exception.
        
        Returns:
            User-friendly error message string
        """
        return _get_error_message(exc)
    
    def _log_error(self, operation: str, pk: str, sk: str, exc: Exception) -> None:
        """Log error with full context including AWS error code.
        
        Args:
            operation: Name of the operation (e.g., 'get_item', 'put_item')
            pk: Partition key value
            sk: Sort key value
            exc: Exception that occurred
        """
        _log_error(operation, pk, sk, exc)
    
    def _get_client(self):
        """Get or create boto3 DynamoDB client (sync, called from to_thread)."""
        if self._client is None:
            try:
                self._client = self.boto3.client(
                    "dynamodb",
                    region_name=settings.AWS_REGION,
                    aws_access_key_id=settings.AWS_ACCESS_KEY_ID or None,
                    aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY or None,
                )
            except Exception as exc:
                logger.error(f"Failed to initialize DynamoDB client: {exc}")
                raise
        return self._client
    
    def _get_table(self):
        """Get or create boto3 DynamoDB table resource (sync, called from to_thread)."""
        if self._table is None:
            try:
                dynamodb = self.boto3.resource(
                    "dynamodb",
                    region_name=settings.AWS_REGION,
                    aws_access_key_id=settings.AWS_ACCESS_KEY_ID or None,
                    aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY or None,
                )
                self._table = dynamodb.Table(settings.DYNAMODB_TABLE_NAME)
            except Exception as exc:
                logger.error(f"Failed to initialize DynamoDB table resource: {exc}")
                raise
        return self._table
    
    async def put_item(self, item: dict) -> dict:
        """Put/insert an item into DynamoDB.
        
        Args:
            item: Item dict with required PK and SK attributes
        
        Returns:
            The inserted item
        
        Raises:
            ValueError: If item is missing PK or SK
            Exception: If DynamoDB operation fails
        """
        # Validate required attributes
        if "PK" not in item or "SK" not in item:
            raise ValueError("Item must have PK and SK attributes")
        
        pk = item.get("PK")
        sk = item.get("SK")
        logger.debug(f"Putting item: PK={pk}, SK={sk}")
        
        try:
            def _do_put():
                table = self._get_table()
                table.put_item(Item=item)
                return item
            
            result = await asyncio.to_thread(_do_put)
            logger.info(f"✓ Put item: PK={pk}, SK={sk}")
            return result
        except Exception as exc:
            self._log_error("put_item", pk, sk, exc)
            raise
    
    async def update_item(
        self,
        pk: str,
        sk: str,
        update_expr: str,
        attr_values: dict,
        attr_names: Optional[dict] = None,
    ) -> dict:
        """Update an existing item in DynamoDB.
        
        Args:
            pk: Partition key value
            sk: Sort key value
            update_expr: UpdateExpression (e.g., "SET #name = :val, #status = :status")
            attr_values: ExpressionAttributeValues (e.g., {":val": "new_value", ":status": "active"})
            attr_names: ExpressionAttributeNames for reserved words (e.g., {"#name": "name"})
        
        Returns:
            Updated item with all attributes
        
        Raises:
            Exception: If DynamoDB operation fails
        """
        logger.debug(f"Updating item: PK={pk}, SK={sk}, expr={update_expr}")
        
        try:
            def _do_update():
                table = self._get_table()
                kwargs = {
                    "Key": {"PK": pk, "SK": sk},
                    "UpdateExpression": update_expr,
                    "ExpressionAttributeValues": attr_values,
                    "ReturnValues": "ALL_NEW",
                }
                if attr_names:
                    kwargs["ExpressionAttributeNames"] = attr_names
                
                response = table.update_item(**kwargs)
                return response.get("Attributes", {})
            
            result = await asyncio.to_thread(_do_update)
            logger.info(f"✓ Updated item: PK={pk}, SK={sk}")
            return result
        except Exception as exc:
            self._log_error("update_item", pk, sk, exc)
            raise
    
    async def delete_item(self, pk: str, sk: str) -> bool:
        """Delete an item from DynamoDB by PK and SK.
        
        Args:
            pk: Partition key value
            sk: Sort key value
        
        Returns:
            True if item was deleted, False if not found
        
        Raises:
            Exception: If DynamoDB operation fails
        """
        logger.debug(f"Deleting item: PK={pk}, SK={sk}")
        
        try:
            def _do_delete():
                table = self._get_table()
                response = table.delete_item(
                    Key={"PK": pk, "SK": sk},
                    ReturnValues="ALL_OLD",
                )
                # If Attributes is present, item existed and was deleted
                return bool(response.get("Attributes"))
            
            was_deleted = await asyncio.to_thread(_do_delete)
            if was_deleted:
                logger.info(f"✓ Deleted item: PK={pk}, SK={sk}")
            else:
                logger.warning(f"Item not found for deletion: PK={pk}, SK={sk}")
            return was_deleted
        except Exception as exc:
            self._log_error("delete_item", pk, sk, exc)
            raise
    
    async def get_item(self, pk: str, sk: str) -> Optional[dict]:
        """Get single item from table by PK and SK.
        
        Retrieves a single item from DynamoDB using the partition key (PK)
        and sort key (SK). Returns None if item not found.
        
        Args:
            pk: Partition key value (e.g., "WORKSPACE#123")
            sk: Sort key value (e.g., "META" or "MEMBER#456")
        
        Returns:
            dict containing the item if found, None if not found
            
        Raises:
            ValueError: If pk or sk are invalid
            Exception: If DynamoDB operation fails
        """
        if not pk or not isinstance(pk, str):
            raise ValueError("pk must be a non-empty string")
        if not sk or not isinstance(sk, str):
            raise ValueError("sk must be a non-empty string")
        
        logger.debug(f"Getting item: PK={pk}, SK={sk}")
        
        try:
            def _do_get():
                table = self._get_table()
                response = table.get_item(Key={"PK": pk, "SK": sk})
                return response.get("Item")
            
            item = await asyncio.to_thread(_do_get)
            
            if item:
                logger.debug(f"✓ Retrieved item: PK={pk}, SK={sk}")
            else:
                logger.debug(f"Item not found: PK={pk}, SK={sk}")
            
            return item
            
        except Exception as exc:
            self._log_error("get_item", pk, sk, exc)
            raise
    
    async def query(
        self, 
        pk: str, 
        sk_prefix: Optional[str] = None, 
        limit: int = 100
    ) -> list[dict]:
        """Query items by partition key with optional sort key prefix filter.
        
        Queries all items with a given partition key (PK), optionally filtering
        by sort key prefix. Supports pagination with limit parameter.
        
        Args:
            pk: Partition key value (e.g., "WORKSPACE#123")
            sk_prefix: Optional sort key prefix to filter results 
                      (e.g., "MEMBER#" to get all members, "CASE#" to get all cases)
            limit: Maximum number of items to return (default: 100, max: 1000)
        
        Returns:
            List of matching items (empty list if no matches)
            
        Raises:
            ValueError: If pk is invalid or limit is out of range
            Exception: If DynamoDB operation fails
        """
        if not pk or not isinstance(pk, str):
            raise ValueError("pk must be a non-empty string")
        if limit < 1 or limit > 1000:
            raise ValueError("limit must be between 1 and 1000")
        
        logger.debug(f"Querying items: PK={pk}, sk_prefix={sk_prefix}, limit={limit}")
        
        try:
            def _do_query():
                table = self._get_table()
                
                # Build query parameters
                query_params = {
                    "KeyConditionExpression": "PK = :pk",
                    "ExpressionAttributeValues": {":pk": pk},
                    "Limit": limit,
                }
                
                # Add sort key prefix filter if provided
                if sk_prefix:
                    query_params["KeyConditionExpression"] += " AND begins_with(SK, :sk_prefix)"
                    query_params["ExpressionAttributeValues"][":sk_prefix"] = sk_prefix
                
                response = table.query(**query_params)
                return response.get("Items", [])
            
            items = await asyncio.to_thread(_do_query)
            
            logger.debug(f"✓ Query completed: PK={pk}, sk_prefix={sk_prefix}, items_returned={len(items)}")
            
            return items
            
        except Exception as exc:
            self._log_error("query", pk, sk_prefix or "null", exc)
            raise
    
    async def scan(
        self, 
        filter_expression: Optional[str] = None, 
        limit: int = 100
    ) -> list[dict]:
        """Scan table with optional filter expression.
        
        Performs a full table scan with optional filtering. Supports pagination
        with limit parameter. Warning: table scans are expensive in production.
        
        Args:
            filter_expression: Optional DynamoDB filter expression to filter items
                              (e.g., "attribute_exists(is_deleted)")
            limit: Maximum number of items to return (default: 100, max: 1000)
        
        Returns:
            List of matching items (empty list if no matches)
            
        Raises:
            ValueError: If limit is out of range
            Exception: If DynamoDB operation fails
        """
        if limit < 1 or limit > 1000:
            raise ValueError("limit must be between 1 and 1000")
        
        logger.debug(f"Scanning table: filter_expression={filter_expression}, limit={limit}")
        
        try:
            def _do_scan():
                table = self._get_table()
                
                # Build scan parameters
                scan_params = {
                    "Limit": limit,
                }
                
                # Add filter expression if provided
                if filter_expression:
                    scan_params["FilterExpression"] = filter_expression
                
                response = table.scan(**scan_params)
                return response.get("Items", [])
            
            items = await asyncio.to_thread(_do_scan)
            
            logger.debug(f"✓ Scan completed: items_returned={len(items)}")
            
            return items
            
        except Exception as exc:
            self._log_error("scan", "n/a", "n/a", exc)
            raise
    
    async def transact_write(self, transact_items: list[dict]) -> bool:
        """Execute atomic multi-item write transaction.
        
        Performs atomic multi-item writes using DynamoDB transact_write_items.
        All items succeed or all rollback (atomic operation). Validates transaction
        size limits per AWS constraints.
        
        Args:
            transact_items: List of transaction items. Each item must be a dict with one of:
                - 'Put': {'Item': {...item dict with PK and SK...}}
                - 'Update': {'Key': {'PK': '...', 'SK': '...'}, 'UpdateExpression': '...', ...}
                - 'Delete': {'Key': {'PK': '...', 'SK': '...'}}
        
        Returns:
            True if transaction succeeds
        
        Raises:
            ValueError: If transact_items is invalid (empty, >25 items, malformed items)
            Exception: If DynamoDB transaction fails or service error occurs
        """
        # Validate transaction items
        if not transact_items:
            raise ValueError("transact_items cannot be empty")
        
        if len(transact_items) > 25:
            raise ValueError(
                f"Transaction contains {len(transact_items)} items; AWS limit is 25 items per transaction"
            )
        
        # Validate each item has exactly one key (Put, Update, or Delete)
        for idx, item in enumerate(transact_items):
            valid_keys = {"Put", "Update", "Delete"}
            item_keys = set(item.keys())
            if not item_keys.issubset(valid_keys):
                raise ValueError(
                    f"Item {idx} has invalid keys: {item_keys}. Must be one of {valid_keys}"
                )
            if len(item_keys) != 1:
                raise ValueError(
                    f"Item {idx} must have exactly one key (Put, Update, or Delete), got {len(item_keys)}"
                )
        
        logger.debug(f"Starting transaction with {len(transact_items)} items")
        
        try:
            def _do_transact():
                table = self._get_table()
                
                # Use the table's transact_write_items method which handles
                # conversion from high-level format to DynamoDB TypedDict format
                try:
                    # Build TransactWriteItem list
                    transact_write_items = []
                    for item in transact_items:
                        if "Put" in item:
                            transact_write_items.append({
                                "Put": item["Put"]
                            })
                        elif "Update" in item:
                            transact_write_items.append({
                                "Update": item["Update"]
                            })
                        elif "Delete" in item:
                            transact_write_items.append({
                                "Delete": item["Delete"]
                            })
                    
                    # Execute transaction using the high-level resource method
                    with table.batch_writer(
                        overwrite_by_pkeys=["PK", "SK"]
                    ) as batch:
                        # For transact_write_items, we need to use the client API
                        # instead of the resource API, because batch_writer doesn't
                        # support transactional semantics
                        pass
                    
                    # Use client's transact_write_items for atomic transactions
                    client = self._get_client()
                    
                    # Convert high-level format to low-level client format
                    client_transact_items = []
                    for item in transact_items:
                        if "Put" in item:
                            put_item = item["Put"]["Item"]
                            # Convert to DynamoDB TypedDict format
                            dynamodb_item = self._serialize_item(put_item)
                            client_transact_items.append({
                                "Put": {
                                    "TableName": settings.DYNAMODB_TABLE_NAME,
                                    "Item": dynamodb_item,
                                }
                            })
                        elif "Update" in item:
                            update_data = item["Update"]
                            # Convert Key to TypedDict format
                            dynamodb_key = self._serialize_item(update_data["Key"])
                            transact_item = {
                                "Update": {
                                    "TableName": settings.DYNAMODB_TABLE_NAME,
                                    "Key": dynamodb_key,
                                    "UpdateExpression": update_data["UpdateExpression"],
                                }
                            }
                            # Add optional parameters if provided
                            if "ExpressionAttributeValues" in update_data:
                                transact_item["Update"]["ExpressionAttributeValues"] = \
                                    self._serialize_item(update_data["ExpressionAttributeValues"])
                            if "ExpressionAttributeNames" in update_data:
                                transact_item["Update"]["ExpressionAttributeNames"] = \
                                    update_data["ExpressionAttributeNames"]
                            if "ConditionExpression" in update_data:
                                transact_item["Update"]["ConditionExpression"] = \
                                    update_data["ConditionExpression"]
                            client_transact_items.append(transact_item)
                        elif "Delete" in item:
                            delete_data = item["Delete"]
                            dynamodb_key = self._serialize_item(delete_data["Key"])
                            transact_item = {
                                "Delete": {
                                    "TableName": settings.DYNAMODB_TABLE_NAME,
                                    "Key": dynamodb_key,
                                }
                            }
                            # Add optional ConditionExpression if provided
                            if "ConditionExpression" in delete_data:
                                transact_item["Delete"]["ConditionExpression"] = \
                                    delete_data["ConditionExpression"]
                            client_transact_items.append(transact_item)
                    
                    # Execute transaction
                    response = client.transact_write_items(
                        TransactItems=client_transact_items
                    )
                    
                    return True
                except Exception:
                    raise
            
            result = await asyncio.to_thread(_do_transact)
            logger.info(f"✓ Transaction successful: {len(transact_items)} items")
            return result
            
        except Exception as exc:
            # For transactions, log with generic PK/SK since multiple items involved
            self._log_error("transact_write", f"multiple_items({len(transact_items)})", "multi", exc)
            raise
    
    def _serialize_item(self, item: dict) -> dict:
        """Convert high-level Python types to DynamoDB TypedDict format.
        
        Converts items like {'PK': 'value', 'count': 5} to
        {'PK': {'S': 'value'}, 'count': {'N': '5'}}
        
        Args:
            item: High-level Python dict
        
        Returns:
            DynamoDB TypedDict format
        """
        dynamodb_item = {}
        for key, value in item.items():
            if isinstance(value, str):
                dynamodb_item[key] = {'S': value}
            elif isinstance(value, (int, float)):
                dynamodb_item[key] = {'N': str(value)}
            elif isinstance(value, bool):
                dynamodb_item[key] = {'BOOL': value}
            elif isinstance(value, bytes):
                dynamodb_item[key] = {'B': value}
            elif isinstance(value, list):
                # Simple list handling for strings
                if all(isinstance(v, str) for v in value):
                    dynamodb_item[key] = {'SS': value}
                else:
                    dynamodb_item[key] = {'L': [self._serialize_value(v) for v in value]}
            elif isinstance(value, dict):
                # Handle nested dicts
                dynamodb_item[key] = {'M': self._serialize_item(value)}
            elif value is None:
                dynamodb_item[key] = {'NULL': True}
            else:
                # Default to string
                dynamodb_item[key] = {'S': str(value)}
        return dynamodb_item
    
    def _serialize_value(self, value: any) -> dict:
        """Convert a single value to DynamoDB type format."""
        if isinstance(value, str):
            return {'S': value}
        elif isinstance(value, (int, float)):
            return {'N': str(value)}
        elif isinstance(value, bool):
            return {'BOOL': value}
        elif isinstance(value, bytes):
            return {'B': value}
        elif isinstance(value, dict):
            return {'M': self._serialize_item(value)}
        elif isinstance(value, list):
            if all(isinstance(v, str) for v in value):
                return {'SS': value}
            else:
                return {'L': [self._serialize_value(v) for v in value]}
        elif value is None:
            return {'NULL': True}
        else:
            return {'S': str(value)}


# Global client instance
_client: Optional[DynamoDBClient] = None


def get_dynamodb_client() -> DynamoDBClient:
    """Get or create the global DynamoDB client instance.
    
    Returns:
        DynamoDBClient instance
    
    Raises:
        RuntimeError: If DynamoDB client initialization fails
    """
    global _client
    if _client is None:
        _client = DynamoDBClient()
    return _client
