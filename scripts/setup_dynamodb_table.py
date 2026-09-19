#!/usr/bin/env python3
"""
DynamoDB Table Setup Script

Creates the compliance-platform DynamoDB table with the correct schema for
the AWS Hackathon project. This script is idempotent and safe to run multiple times.

Usage:
    python scripts/setup_dynamodb_table.py

Environment Variables:
    AWS_REGION              AWS region (default: us-east-1)
    AWS_ACCESS_KEY_ID       AWS access key (required)
    AWS_SECRET_ACCESS_KEY   AWS secret key (required)
    DYNAMODB_TABLE_NAME     Table name (default: compliance-platform)

Exit Codes:
    0 - Success (table created or already exists)
    1 - Error (missing credentials, network error, etc.)
"""

import os
import sys
import logging
from pathlib import Path

import boto3
from botocore.exceptions import ClientError, NoCredentialsError, EndpointConnectionError

# ============================================================================
# Logging Configuration
# ============================================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)-8s | %(message)s",
)
logger = logging.getLogger(__name__)

# ============================================================================
# Configuration from Environment
# ============================================================================

AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")
AWS_ACCESS_KEY_ID = os.environ.get("AWS_ACCESS_KEY_ID", "")
AWS_SECRET_ACCESS_KEY = os.environ.get("AWS_SECRET_ACCESS_KEY", "")
DYNAMODB_TABLE_NAME = os.environ.get("DYNAMODB_TABLE_NAME", "compliance-platform")


def validate_credentials():
    """Validate that AWS credentials are configured."""
    if not AWS_ACCESS_KEY_ID:
        logger.error("AWS_ACCESS_KEY_ID environment variable not set")
        return False
    if not AWS_SECRET_ACCESS_KEY:
        logger.error("AWS_SECRET_ACCESS_KEY environment variable not set")
        return False
    return True


def create_dynamodb_client():
    """Create and return a boto3 DynamoDB resource."""
    try:
        client = boto3.resource(
            "dynamodb",
            region_name=AWS_REGION,
            aws_access_key_id=AWS_ACCESS_KEY_ID,
            aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
        )
        return client
    except NoCredentialsError as e:
        logger.error("AWS credentials not found: %s", e)
        return None
    except Exception as e:
        logger.error("Failed to create DynamoDB client: %s", e)
        return None


def table_exists(dynamodb, table_name):
    """Check if a DynamoDB table exists."""
    try:
        table = dynamodb.Table(table_name)
        # Try to load the table's metadata; this will fail if table doesn't exist
        table.load()
        return True
    except ClientError as e:
        if e.response["Error"]["Code"] == "ResourceNotFoundException":
            return False
        logger.error("Error checking table existence: %s", e)
        raise
    except Exception as e:
        logger.error("Unexpected error checking table existence: %s", e)
        raise


def describe_table(dynamodb, table_name):
    """Get detailed information about a DynamoDB table."""
    try:
        table = dynamodb.Table(table_name)
        table.load()
        
        # Extract key information
        key_schema = table.key_schema
        billing_mode = table.billing_mode_summary.get("BillingMode", "UNKNOWN")
        status = table.table_status
        
        logger.info("✓ Table '%s' already exists", table_name)
        logger.info("  Status: %s", status)
        logger.info("  Billing Mode: %s", billing_mode)
        logger.info("  Key Schema:")
        for key_spec in key_schema:
            attr_name = key_spec["AttributeName"]
            key_type = key_spec["KeyType"]
            logger.info("    - %s (%s)", attr_name, key_type)
        
        # Check TTL
        try:
            ttl_response = dynamodb.meta.client.describe_time_to_live(
                TableName=table_name
            )
            ttl_status = ttl_response.get("TimeToLiveDescription", {}).get("TimeToLiveStatus", "DISABLED")
            logger.info("  TTL: %s", ttl_status)
        except ClientError as e:
            if e.response["Error"]["Code"] != "ResourceNotFoundException":
                logger.warning("  TTL: Unable to check (error: %s)", e)
        
        return True
    except Exception as e:
        logger.error("Error describing table: %s", e)
        return False


def create_table(dynamodb, table_name):
    """Create the compliance-platform DynamoDB table.
    
    Schema:
        - Partition Key (PK): String
        - Sort Key (SK): String
        - Billing Mode: On-Demand (pay-per-request)
        - TTL: Optional (on 'ttl' attribute)
    """
    try:
        logger.info("Creating table '%s'...", table_name)
        
        table = dynamodb.create_table(
            TableName=table_name,
            KeySchema=[
                {"AttributeName": "PK", "KeyType": "HASH"},      # Partition Key
                {"AttributeName": "SK", "KeyType": "RANGE"},     # Sort Key
            ],
            AttributeDefinitions=[
                {"AttributeName": "PK", "AttributeType": "S"},   # String
                {"AttributeName": "SK", "AttributeType": "S"},   # String
            ],
            BillingMode="PAY_PER_REQUEST",  # On-demand (pay-per-request)
        )
        
        # Wait for table to be created
        logger.info("Waiting for table to be created (this may take a minute)...")
        table.wait_until_exists()
        
        logger.info("✓ Table '%s' created successfully", table_name)
        logger.info("  Status: %s", table.table_status)
        logger.info("  Billing Mode: PAY_PER_REQUEST")
        logger.info("  Key Schema:")
        logger.info("    - PK (Partition Key, String)")
        logger.info("    - SK (Sort Key, String)")
        
        return True
        
    except ClientError as e:
        if e.response["Error"]["Code"] == "ResourceInUseException":
            logger.warning("Table '%s' is already being created or updated", table_name)
            return False
        logger.error("Failed to create table: %s", e)
        return False
    except Exception as e:
        logger.error("Unexpected error creating table: %s", e)
        return False


def enable_ttl(dynamodb_client, table_name):
    """Enable TTL on the 'ttl' attribute (optional).
    
    This is optional and used for future auto-expiration of records.
    """
    try:
        logger.info("Enabling TTL on 'ttl' attribute...")
        
        dynamodb_client.update_time_to_live(
            TableName=table_name,
            TimeToLiveSpecification={
                "AttributeName": "ttl",
                "Enabled": True,
            },
        )
        
        logger.info("✓ TTL enabled on 'ttl' attribute")
        return True
        
    except ClientError as e:
        if e.response["Error"]["Code"] == "ValidationException":
            # TTL might already be enabled or invalid
            logger.warning("Could not enable TTL: %s", e)
            return False
        logger.error("Failed to enable TTL: %s", e)
        return False
    except Exception as e:
        logger.error("Unexpected error enabling TTL: %s", e)
        return False


def main():
    """Main entry point for the setup script."""
    logger.info("=" * 70)
    logger.info("DynamoDB Table Setup for Compliance Platform")
    logger.info("=" * 70)
    logger.info("AWS Region: %s", AWS_REGION)
    logger.info("Table Name: %s", DYNAMODB_TABLE_NAME)
    logger.info("")
    
    # Validate credentials
    if not validate_credentials():
        logger.error("AWS credentials not properly configured")
        return 1
    
    # Create DynamoDB client
    dynamodb = create_dynamodb_client()
    if dynamodb is None:
        logger.error("Failed to initialize DynamoDB client")
        return 1
    
    try:
        # Check if table already exists
        if table_exists(dynamodb, DYNAMODB_TABLE_NAME):
            logger.info("")
            logger.info("Table is ready for use!")
            describe_table(dynamodb, DYNAMODB_TABLE_NAME)
            logger.info("")
            return 0
        
        # Table doesn't exist, create it
        logger.info("")
        if not create_table(dynamodb, DYNAMODB_TABLE_NAME):
            logger.error("Failed to create table")
            return 1
        
        logger.info("")
        
        # Enable TTL (optional, best-effort)
        logger.info("Configuring optional features...")
        enable_ttl(dynamodb.meta.client, DYNAMODB_TABLE_NAME)
        
        logger.info("")
        logger.info("=" * 70)
        logger.info("✓ Setup Complete!")
        logger.info("=" * 70)
        logger.info("")
        logger.info("Next Steps:")
        logger.info("1. Verify the table in AWS Console")
        logger.info("2. Configure environment variables in backend/.env:")
        logger.info("     AWS_REGION=%s", AWS_REGION)
        logger.info("     AWS_ACCESS_KEY_ID=<your-access-key>")
        logger.info("     AWS_SECRET_ACCESS_KEY=<your-secret-key>")
        logger.info("     DYNAMODB_TABLE_NAME=%s", DYNAMODB_TABLE_NAME)
        logger.info("3. Start the backend application")
        logger.info("")
        
        return 0
        
    except EndpointConnectionError as e:
        logger.error("Failed to connect to AWS: %s", e)
        logger.error("Check your AWS region and credentials")
        return 1
    except NoCredentialsError as e:
        logger.error("AWS credentials error: %s", e)
        return 1
    except Exception as e:
        logger.error("Unexpected error: %s", e)
        return 1


if __name__ == "__main__":
    sys.exit(main())
