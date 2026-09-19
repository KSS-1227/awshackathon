"""DynamoDB schema validators for all item types.

This module provides validators for DynamoDB items across all supported item types:
1. WORKSPACE#{workspace_id}+META: Workspace metadata
2. WORKSPACE#{workspace_id}+MEMBER#{user_id}: Workspace members
3. USER#{user_id}+WORKSPACE#{workspace_id}: Reverse-index for user workspace listing
4. WORKSPACE#{workspace_id}+CASE#{case_id}: Cases within workspace
5. WORKSPACE#{workspace_id}+AUDIT#{timestamp}: Audit logs
6. WORKSPACE#{workspace_id}+JOB#{job_id}: Async jobs

Each validator checks:
- Required fields are present
- Field types are correct
- Length/value constraints are satisfied
- PK/SK format is valid

Validators raise ValueError with descriptive messages on validation failure.
"""


class WorkspaceMetaValidator:
    """Validator for WORKSPACE#{workspace_id}+META items.
    
    Schema:
        PK: str = "WORKSPACE#{workspace_id}"
        SK: str = "META"
        name: str = workspace name (3-80 chars)
        owner_id: str = user ID of workspace owner
        s3_bucket: str = S3 bucket for document storage
        status: str = "active" | "archived" | "deleted"
        is_deleted: bool = soft delete flag (optional)
        deleted_at: int = Unix timestamp when deleted (optional)
        created_at: int = Unix timestamp when created
        updated_at: int = Unix timestamp when last updated
    """

    @staticmethod
    def validate(item: dict) -> None:
        """Validate a workspace metadata item.
        
        Args:
            item: DynamoDB item dict to validate
            
        Raises:
            ValueError: If validation fails with descriptive message
        """
        # Check required fields
        required_fields = ["PK", "SK", "name", "owner_id", "s3_bucket", "status", "created_at", "updated_at"]
        for field in required_fields:
            if field not in item:
                raise ValueError(f"WorkspaceMetaValidator: Missing required field '{field}'")

        # Validate PK/SK format
        pk = item.get("PK", "")
        sk = item.get("SK", "")
        if not pk.startswith("WORKSPACE#"):
            raise ValueError(f"WorkspaceMetaValidator: PK must start with 'WORKSPACE#', got '{pk}'")
        if sk != "META":
            raise ValueError(f"WorkspaceMetaValidator: SK must be 'META', got '{sk}'")

        # Validate field types
        if not isinstance(item.get("name"), str):
            raise ValueError(f"WorkspaceMetaValidator: 'name' must be string, got {type(item.get('name')).__name__}")
        if not isinstance(item.get("owner_id"), str):
            raise ValueError(f"WorkspaceMetaValidator: 'owner_id' must be string, got {type(item.get('owner_id')).__name__}")
        if not isinstance(item.get("s3_bucket"), str):
            raise ValueError(f"WorkspaceMetaValidator: 's3_bucket' must be string, got {type(item.get('s3_bucket')).__name__}")
        if not isinstance(item.get("status"), str):
            raise ValueError(f"WorkspaceMetaValidator: 'status' must be string, got {type(item.get('status')).__name__}")
        if not isinstance(item.get("created_at"), int):
            raise ValueError(f"WorkspaceMetaValidator: 'created_at' must be int, got {type(item.get('created_at')).__name__}")
        if not isinstance(item.get("updated_at"), int):
            raise ValueError(f"WorkspaceMetaValidator: 'updated_at' must be int, got {type(item.get('updated_at')).__name__}")

        # Validate name length (3-80 characters)
        name = item.get("name", "")
        if len(name) < 3:
            raise ValueError(f"WorkspaceMetaValidator: 'name' must be at least 3 characters, got {len(name)}")
        if len(name) > 80:
            raise ValueError(f"WorkspaceMetaValidator: 'name' must be at most 80 characters, got {len(name)}")

        # Validate status value
        valid_statuses = ["active", "archived", "deleted"]
        if item.get("status") not in valid_statuses:
            raise ValueError(f"WorkspaceMetaValidator: 'status' must be one of {valid_statuses}, got '{item.get('status')}'")

        # Optional fields: is_deleted, deleted_at
        if "is_deleted" in item and not isinstance(item["is_deleted"], bool):
            raise ValueError(f"WorkspaceMetaValidator: 'is_deleted' must be bool, got {type(item['is_deleted']).__name__}")
        if "deleted_at" in item and not isinstance(item["deleted_at"], int):
            raise ValueError(f"WorkspaceMetaValidator: 'deleted_at' must be int, got {type(item['deleted_at']).__name__}")


class WorkspaceMemberValidator:
    """Validator for WORKSPACE#{workspace_id}+MEMBER#{user_id} items.
    
    Schema:
        PK: str = "WORKSPACE#{workspace_id}"
        SK: str = "MEMBER#{user_id}"
        email: str = user email address
        role: str = "Admin" | "Analyst" | "Viewer"
        membership_status: str = "active" | "pending" | "removed"
        added_at: int = Unix timestamp when added
        removed_at: int = Unix timestamp when removed (optional)
    """

    @staticmethod
    def validate(item: dict) -> None:
        """Validate a workspace member item.
        
        Args:
            item: DynamoDB item dict to validate
            
        Raises:
            ValueError: If validation fails with descriptive message
        """
        # Check required fields
        required_fields = ["PK", "SK", "email", "role", "membership_status", "added_at"]
        for field in required_fields:
            if field not in item:
                raise ValueError(f"WorkspaceMemberValidator: Missing required field '{field}'")

        # Validate PK/SK format
        pk = item.get("PK", "")
        sk = item.get("SK", "")
        if not pk.startswith("WORKSPACE#"):
            raise ValueError(f"WorkspaceMemberValidator: PK must start with 'WORKSPACE#', got '{pk}'")
        if not sk.startswith("MEMBER#"):
            raise ValueError(f"WorkspaceMemberValidator: SK must start with 'MEMBER#', got '{sk}'")

        # Validate field types
        if not isinstance(item.get("email"), str):
            raise ValueError(f"WorkspaceMemberValidator: 'email' must be string, got {type(item.get('email')).__name__}")
        if not isinstance(item.get("role"), str):
            raise ValueError(f"WorkspaceMemberValidator: 'role' must be string, got {type(item.get('role')).__name__}")
        if not isinstance(item.get("membership_status"), str):
            raise ValueError(f"WorkspaceMemberValidator: 'membership_status' must be string, got {type(item.get('membership_status')).__name__}")
        if not isinstance(item.get("added_at"), int):
            raise ValueError(f"WorkspaceMemberValidator: 'added_at' must be int, got {type(item.get('added_at')).__name__}")

        # Validate role value
        valid_roles = ["Admin", "Analyst", "Viewer"]
        if item.get("role") not in valid_roles:
            raise ValueError(f"WorkspaceMemberValidator: 'role' must be one of {valid_roles}, got '{item.get('role')}'")

        # Validate membership_status value
        valid_statuses = ["active", "pending", "removed"]
        if item.get("membership_status") not in valid_statuses:
            raise ValueError(f"WorkspaceMemberValidator: 'membership_status' must be one of {valid_statuses}, got '{item.get('membership_status')}'")

        # Optional field: removed_at
        if "removed_at" in item and not isinstance(item["removed_at"], int):
            raise ValueError(f"WorkspaceMemberValidator: 'removed_at' must be int, got {type(item['removed_at']).__name__}")


class UserWorkspaceValidator:
    """Validator for USER#{user_id}+WORKSPACE#{workspace_id} reverse-index items.
    
    Schema:
        PK: str = "USER#{user_id}"
        SK: str = "WORKSPACE#{workspace_id}"
        role: str = "Admin" | "Analyst" | "Viewer"
        membership_status: str = "active" | "pending" | "removed"
        joined_at: int = Unix timestamp when joined
    """

    @staticmethod
    def validate(item: dict) -> None:
        """Validate a user-workspace reverse-index item.
        
        Args:
            item: DynamoDB item dict to validate
            
        Raises:
            ValueError: If validation fails with descriptive message
        """
        # Check required fields
        required_fields = ["PK", "SK", "role", "membership_status", "joined_at"]
        for field in required_fields:
            if field not in item:
                raise ValueError(f"UserWorkspaceValidator: Missing required field '{field}'")

        # Validate PK/SK format
        pk = item.get("PK", "")
        sk = item.get("SK", "")
        if not pk.startswith("USER#"):
            raise ValueError(f"UserWorkspaceValidator: PK must start with 'USER#', got '{pk}'")
        if not sk.startswith("WORKSPACE#"):
            raise ValueError(f"UserWorkspaceValidator: SK must start with 'WORKSPACE#', got '{sk}'")

        # Validate field types
        if not isinstance(item.get("role"), str):
            raise ValueError(f"UserWorkspaceValidator: 'role' must be string, got {type(item.get('role')).__name__}")
        if not isinstance(item.get("membership_status"), str):
            raise ValueError(f"UserWorkspaceValidator: 'membership_status' must be string, got {type(item.get('membership_status')).__name__}")
        if not isinstance(item.get("joined_at"), int):
            raise ValueError(f"UserWorkspaceValidator: 'joined_at' must be int, got {type(item.get('joined_at')).__name__}")

        # Validate role value
        valid_roles = ["Admin", "Analyst", "Viewer"]
        if item.get("role") not in valid_roles:
            raise ValueError(f"UserWorkspaceValidator: 'role' must be one of {valid_roles}, got '{item.get('role')}'")

        # Validate membership_status value
        valid_statuses = ["active", "pending", "removed"]
        if item.get("membership_status") not in valid_statuses:
            raise ValueError(f"UserWorkspaceValidator: 'membership_status' must be one of {valid_statuses}, got '{item.get('membership_status')}'")


class WorkspaceCaseValidator:
    """Validator for WORKSPACE#{workspace_id}+CASE#{case_id} items.
    
    Schema:
        PK: str = "WORKSPACE#{workspace_id}"
        SK: str = "CASE#{case_id}"
        title: str = case title (1-200 chars)
        created_by: str = user ID of case creator
        status: str = "open" | "closed" | "archived"
        document_count: int = number of documents in case
        graph_status: str = "pending" | "processing" | "completed" | "failed"
        graph_id: str = S3 path to graph file (optional)
        created_at: int = Unix timestamp when created
        updated_at: int = Unix timestamp when last updated
        is_deleted: bool = soft delete flag (optional)
    """

    @staticmethod
    def validate(item: dict) -> None:
        """Validate a workspace case item.
        
        Args:
            item: DynamoDB item dict to validate
            
        Raises:
            ValueError: If validation fails with descriptive message
        """
        # Check required fields
        required_fields = ["PK", "SK", "title", "created_by", "status", "document_count", "graph_status", "created_at"]
        for field in required_fields:
            if field not in item:
                raise ValueError(f"WorkspaceCaseValidator: Missing required field '{field}'")

        # Validate PK/SK format
        pk = item.get("PK", "")
        sk = item.get("SK", "")
        if not pk.startswith("WORKSPACE#"):
            raise ValueError(f"WorkspaceCaseValidator: PK must start with 'WORKSPACE#', got '{pk}'")
        if not sk.startswith("CASE#"):
            raise ValueError(f"WorkspaceCaseValidator: SK must start with 'CASE#', got '{sk}'")

        # Validate field types
        if not isinstance(item.get("title"), str):
            raise ValueError(f"WorkspaceCaseValidator: 'title' must be string, got {type(item.get('title')).__name__}")
        if not isinstance(item.get("created_by"), str):
            raise ValueError(f"WorkspaceCaseValidator: 'created_by' must be string, got {type(item.get('created_by')).__name__}")
        if not isinstance(item.get("status"), str):
            raise ValueError(f"WorkspaceCaseValidator: 'status' must be string, got {type(item.get('status')).__name__}")
        if not isinstance(item.get("document_count"), int):
            raise ValueError(f"WorkspaceCaseValidator: 'document_count' must be int, got {type(item.get('document_count')).__name__}")
        if not isinstance(item.get("graph_status"), str):
            raise ValueError(f"WorkspaceCaseValidator: 'graph_status' must be string, got {type(item.get('graph_status')).__name__}")
        if not isinstance(item.get("created_at"), int):
            raise ValueError(f"WorkspaceCaseValidator: 'created_at' must be int, got {type(item.get('created_at')).__name__}")

        # Validate title length (1-200 characters)
        title = item.get("title", "")
        if len(title) < 1:
            raise ValueError(f"WorkspaceCaseValidator: 'title' must be at least 1 character, got {len(title)}")
        if len(title) > 200:
            raise ValueError(f"WorkspaceCaseValidator: 'title' must be at most 200 characters, got {len(title)}")

        # Validate status value
        valid_statuses = ["open", "closed", "archived"]
        if item.get("status") not in valid_statuses:
            raise ValueError(f"WorkspaceCaseValidator: 'status' must be one of {valid_statuses}, got '{item.get('status')}'")

        # Validate graph_status value
        valid_graph_statuses = ["pending", "processing", "completed", "failed"]
        if item.get("graph_status") not in valid_graph_statuses:
            raise ValueError(f"WorkspaceCaseValidator: 'graph_status' must be one of {valid_graph_statuses}, got '{item.get('graph_status')}'")

        # Validate document_count is non-negative
        if item.get("document_count", 0) < 0:
            raise ValueError(f"WorkspaceCaseValidator: 'document_count' must be non-negative, got {item.get('document_count')}")

        # Optional fields: graph_id, updated_at, is_deleted
        if "graph_id" in item and not isinstance(item["graph_id"], str):
            raise ValueError(f"WorkspaceCaseValidator: 'graph_id' must be string, got {type(item['graph_id']).__name__}")
        if "updated_at" in item and not isinstance(item["updated_at"], int):
            raise ValueError(f"WorkspaceCaseValidator: 'updated_at' must be int, got {type(item['updated_at']).__name__}")
        if "is_deleted" in item and not isinstance(item["is_deleted"], bool):
            raise ValueError(f"WorkspaceCaseValidator: 'is_deleted' must be bool, got {type(item['is_deleted']).__name__}")


class WorkspaceAuditValidator:
    """Validator for WORKSPACE#{workspace_id}+AUDIT#{timestamp} items.
    
    Schema:
        PK: str = "WORKSPACE#{workspace_id}"
        SK: str = "AUDIT#{ISO8601_timestamp}"
        event_type: str = event type (e.g., "workspace_created", "member_added")
        actor_id: str = user ID who performed action
        action: str = action description
        detail: str or dict = contextual details (max 2000 chars)
        timestamp: str = ISO 8601 timestamp
    """

    @staticmethod
    def validate(item: dict) -> None:
        """Validate a workspace audit item.
        
        Args:
            item: DynamoDB item dict to validate
            
        Raises:
            ValueError: If validation fails with descriptive message
        """
        # Check required fields
        required_fields = ["PK", "SK", "event_type", "actor_id", "action", "detail", "timestamp"]
        for field in required_fields:
            if field not in item:
                raise ValueError(f"WorkspaceAuditValidator: Missing required field '{field}'")

        # Validate PK/SK format
        pk = item.get("PK", "")
        sk = item.get("SK", "")
        if not pk.startswith("WORKSPACE#"):
            raise ValueError(f"WorkspaceAuditValidator: PK must start with 'WORKSPACE#', got '{pk}'")
        if not sk.startswith("AUDIT#"):
            raise ValueError(f"WorkspaceAuditValidator: SK must start with 'AUDIT#', got '{sk}'")

        # Validate field types
        if not isinstance(item.get("event_type"), str):
            raise ValueError(f"WorkspaceAuditValidator: 'event_type' must be string, got {type(item.get('event_type')).__name__}")
        if not isinstance(item.get("actor_id"), str):
            raise ValueError(f"WorkspaceAuditValidator: 'actor_id' must be string, got {type(item.get('actor_id')).__name__}")
        if not isinstance(item.get("action"), str):
            raise ValueError(f"WorkspaceAuditValidator: 'action' must be string, got {type(item.get('action')).__name__}")
        
        # detail can be string or dict; validate it
        detail = item.get("detail")
        if isinstance(detail, dict):
            # Convert dict to string for length check
            detail_str = str(detail)
        elif isinstance(detail, str):
            detail_str = detail
        else:
            raise ValueError(f"WorkspaceAuditValidator: 'detail' must be string or dict, got {type(detail).__name__}")
        
        if not isinstance(item.get("timestamp"), str):
            raise ValueError(f"WorkspaceAuditValidator: 'timestamp' must be string, got {type(item.get('timestamp')).__name__}")

        # Validate detail length (max 2000 characters)
        if len(detail_str) > 2000:
            raise ValueError(f"WorkspaceAuditValidator: 'detail' must be at most 2000 characters, got {len(detail_str)}")

        # Validate event_type is non-empty
        if not item.get("event_type"):
            raise ValueError(f"WorkspaceAuditValidator: 'event_type' cannot be empty")


class WorkspaceJobValidator:
    """Validator for WORKSPACE#{workspace_id}+JOB#{job_id} items.
    
    Schema:
        PK: str = "WORKSPACE#{workspace_id}"
        SK: str = "JOB#{job_id}"
        job_id: str = unique job identifier
        case_id: str = associated case ID
        status: str = "pending" | "processing" | "completed" | "failed"
        s3_key: str = S3 path to document
        entities_extracted: int = number of entities (optional, only if completed)
        relationships_extracted: int = number of relationships (optional, only if completed)
        error_message: str = error details (optional, only if failed)
        created_at: int = Unix timestamp when created
        updated_at: int = Unix timestamp when last updated
    """

    @staticmethod
    def validate(item: dict) -> None:
        """Validate a workspace job item.
        
        Args:
            item: DynamoDB item dict to validate
            
        Raises:
            ValueError: If validation fails with descriptive message
        """
        # Check required fields
        required_fields = ["PK", "SK", "job_id", "case_id", "status", "s3_key", "created_at", "updated_at"]
        for field in required_fields:
            if field not in item:
                raise ValueError(f"WorkspaceJobValidator: Missing required field '{field}'")

        # Validate PK/SK format
        pk = item.get("PK", "")
        sk = item.get("SK", "")
        if not pk.startswith("WORKSPACE#"):
            raise ValueError(f"WorkspaceJobValidator: PK must start with 'WORKSPACE#', got '{pk}'")
        if not sk.startswith("JOB#"):
            raise ValueError(f"WorkspaceJobValidator: SK must start with 'JOB#', got '{sk}'")

        # Validate field types
        if not isinstance(item.get("job_id"), str):
            raise ValueError(f"WorkspaceJobValidator: 'job_id' must be string, got {type(item.get('job_id')).__name__}")
        if not isinstance(item.get("case_id"), str):
            raise ValueError(f"WorkspaceJobValidator: 'case_id' must be string, got {type(item.get('case_id')).__name__}")
        if not isinstance(item.get("status"), str):
            raise ValueError(f"WorkspaceJobValidator: 'status' must be string, got {type(item.get('status')).__name__}")
        if not isinstance(item.get("s3_key"), str):
            raise ValueError(f"WorkspaceJobValidator: 's3_key' must be string, got {type(item.get('s3_key')).__name__}")
        if not isinstance(item.get("created_at"), int):
            raise ValueError(f"WorkspaceJobValidator: 'created_at' must be int, got {type(item.get('created_at')).__name__}")
        if not isinstance(item.get("updated_at"), int):
            raise ValueError(f"WorkspaceJobValidator: 'updated_at' must be int, got {type(item.get('updated_at')).__name__}")

        # Validate status value
        valid_statuses = ["pending", "processing", "completed", "failed"]
        if item.get("status") not in valid_statuses:
            raise ValueError(f"WorkspaceJobValidator: 'status' must be one of {valid_statuses}, got '{item.get('status')}'")

        # Optional fields: entities_extracted, relationships_extracted, error_message
        if "entities_extracted" in item and not isinstance(item["entities_extracted"], int):
            raise ValueError(f"WorkspaceJobValidator: 'entities_extracted' must be int, got {type(item['entities_extracted']).__name__}")
        if "relationships_extracted" in item and not isinstance(item["relationships_extracted"], int):
            raise ValueError(f"WorkspaceJobValidator: 'relationships_extracted' must be int, got {type(item['relationships_extracted']).__name__}")
        if "error_message" in item and not isinstance(item["error_message"], str):
            raise ValueError(f"WorkspaceJobValidator: 'error_message' must be string, got {type(item['error_message']).__name__}")

        # If status is completed, entities_extracted and relationships_extracted should be present
        if item.get("status") == "completed":
            if "entities_extracted" not in item:
                raise ValueError(f"WorkspaceJobValidator: 'entities_extracted' required when status is 'completed'")
            if "relationships_extracted" not in item:
                raise ValueError(f"WorkspaceJobValidator: 'relationships_extracted' required when status is 'completed'")

        # If status is failed, error_message should be present
        if item.get("status") == "failed" and "error_message" not in item:
            raise ValueError(f"WorkspaceJobValidator: 'error_message' recommended when status is 'failed'")
