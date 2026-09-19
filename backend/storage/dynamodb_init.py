"""
DynamoDB table initialization and verification module.

This module ensures the DynamoDB table exists and is properly configured
before the application starts. It verifies:
- Table existence
- Key schema (PK and SK)
- Billing mode (on-demand)
- Table status (ACTIVE)
"""
import asyncio
import logging
from typing import Optional

import boto3
from botocore.exceptions import ClientError

from ..config import settings

logger = logging.getLogger(__name__)


async def initialize_dynamodb() -> bool:
    """Initialize and verify DynamoDB table is ready for use.
    
    Checks if the configured DynamoDB table exists and has the correct
    schema and configuration. Logs warnings if issues are found.
    
    Returns:
        True if table exists and is ACTIVE with correct schema
        
    Raises:
        RuntimeError: If table is missing or critically misconfigured
    """
    table_name = settings.DYNAMODB_TABLE_NAME
    
    if not table_name:
        raise RuntimeError(
            "DYNAMODB_TABLE_NAME is not configured. "
            "Set it in your .env file or environment variables."
        )
    
    # Run boto3 operations in thread pool to avoid blocking
    def _check_table():
        try:
            client = boto3.client(
                "dynamodb",
                region_name=settings.AWS_REGION,
                aws_access_key_id=settings.AWS_ACCESS_KEY_ID or None,
                aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY or None,
            )
            
            response = client.describe_table(TableName=table_name)
            return response.get("Table", {})
        except ClientError as exc:
            error_code = exc.response.get("Error", {}).get("Code", "Unknown")
            if error_code == "ResourceNotFoundException":
                raise RuntimeError(
                    f"DynamoDB table '{table_name}' does not exist. "
                    f"Create it before starting the application."
                ) from exc
            raise RuntimeError(
                f"Failed to describe DynamoDB table '{table_name}': {exc}"
            ) from exc
        except Exception as exc:
            raise RuntimeError(
                f"Failed to connect to DynamoDB: {exc}. "
                f"Check AWS credentials and region configuration."
            ) from exc
    
    table_info = await asyncio.to_thread(_check_table)
    
    # Verify table status
    status = table_info.get("TableStatus")
    if status != "ACTIVE":
        raise RuntimeError(
            f"DynamoDB table '{table_name}' is not ACTIVE (current status: {status}). "
            f"Wait for the table to finish creating/updating before starting."
        )
    
    # Verify key schema
    key_schema = table_info.get("KeySchema", [])
    attributes = table_info.get("AttributeDefinitions", [])
    
    # Expected key schema: one PK and one SK
    if len(key_schema) < 2:
        raise RuntimeError(
            f"DynamoDB table '{table_name}' has invalid key schema. "
            f"Expected PK and SK, got: {key_schema}"
        )
    
    # Verify we have both HASH (PK) and RANGE (SK) keys
    key_types = {ks["AttributeName"]: ks["KeyType"] for ks in key_schema}
    has_pk = any(kt == "HASH" for kt in key_types.values())
    has_sk = any(kt == "RANGE" for kt in key_types.values())
    
    if not has_pk or not has_sk:
        raise RuntimeError(
            f"DynamoDB table '{table_name}' must have both partition key (PK) "
            f"and sort key (SK). Current keys: {key_schema}"
        )
    
    # Check billing mode
    billing_mode = table_info.get("BillingModeSummary", {}).get("BillingMode")
    if billing_mode != "PAY_PER_REQUEST":
        logger.warning(
            f"DynamoDB table '{table_name}' billing mode is '{billing_mode}', "
            f"not 'PAY_PER_REQUEST' (on-demand). Consider updating to on-demand billing."
        )
    
    # Log table metadata
    item_count = table_info.get("ItemCount", 0)
    size_bytes = table_info.get("TableSizeBytes", 0)
    created_at = table_info.get("CreationDateTime")
    
    logger.info(
        f"✓ DynamoDB table '{table_name}' is ready. "
        f"Status: {status}, Items: {item_count}, Size: {size_bytes} bytes, "
        f"Created: {created_at}, Billing: {billing_mode}"
    )
    
    return True


async def check_table_exists(table_name: str) -> bool:
    """Check if a DynamoDB table exists and is ACTIVE.
    
    Args:
        table_name: Name of the DynamoDB table to check
        
    Returns:
        True if table exists and status is ACTIVE, False otherwise
    """
    def _check_exists():
        try:
            client = boto3.client(
                "dynamodb",
                region_name=settings.AWS_REGION,
                aws_access_key_id=settings.AWS_ACCESS_KEY_ID or None,
                aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY or None,
            )
            response = client.describe_table(TableName=table_name)
            status = response.get("Table", {}).get("TableStatus")
            return status == "ACTIVE"
        except ClientError as exc:
            error_code = exc.response.get("Error", {}).get("Code", "Unknown")
            if error_code == "ResourceNotFoundException":
                return False
            logger.warning(f"Error checking if table '{table_name}' exists: {exc}")
            return False
        except Exception as exc:
            logger.warning(f"Error checking DynamoDB table: {exc}")
            return False
    
    return await asyncio.to_thread(_check_exists)


async def get_table_status() -> dict:
    """Get metadata about the configured DynamoDB table.
    
    Returns a dict with table status information including:
    - status: Current table status (CREATING, UPDATING, DELETING, ACTIVE)
    - creation_date: When the table was created
    - item_count: Number of items in the table
    - size_bytes: Size of the table in bytes
    - billing_mode: BillingMode (PAY_PER_REQUEST or PROVISIONED)
    - key_schema: List of key definitions
    
    Returns:
        Dict with table metadata, or empty dict if table doesn't exist
    """
    table_name = settings.DYNAMODB_TABLE_NAME
    
    def _get_status():
        try:
            client = boto3.client(
                "dynamodb",
                region_name=settings.AWS_REGION,
                aws_access_key_id=settings.AWS_ACCESS_KEY_ID or None,
                aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY or None,
            )
            response = client.describe_table(TableName=table_name)
            table = response.get("Table", {})
            
            return {
                "status": table.get("TableStatus"),
                "creation_date": table.get("CreationDateTime"),
                "item_count": table.get("ItemCount", 0),
                "size_bytes": table.get("TableSizeBytes", 0),
                "billing_mode": table.get("BillingModeSummary", {}).get("BillingMode"),
                "key_schema": table.get("KeySchema", []),
            }
        except ClientError as exc:
            error_code = exc.response.get("Error", {}).get("Code", "Unknown")
            if error_code == "ResourceNotFoundException":
                logger.debug(f"Table '{table_name}' does not exist")
                return {}
            logger.warning(f"Error getting table status: {exc}")
            return {}
        except Exception as exc:
            logger.warning(f"Error connecting to DynamoDB: {exc}")
            return {}
    
    return await asyncio.to_thread(_get_status)
