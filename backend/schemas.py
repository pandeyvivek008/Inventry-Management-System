from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, ConfigDict, Field


class VendorOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    contact: Optional[str] = None


class VariantCreate(BaseModel):
    size: str
    initial_stock: int = 0
    reorder_threshold: int = 5
    target_stock_level: int = 20


class ProductCreate(BaseModel):
    sku: str
    name: str
    brand: Optional[str] = None
    category: Optional[str] = None
    vendor_id: Optional[int] = None
    variants: List[VariantCreate] = Field(min_length=1)


class ProductUpdate(BaseModel):
    name: Optional[str] = None
    brand: Optional[str] = None
    category: Optional[str] = None
    vendor_id: Optional[int] = None


class VariantUpdate(BaseModel):
    reorder_threshold: Optional[int] = None
    target_stock_level: Optional[int] = None


class VariantOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    variant_code: str
    size: str
    current_stock: int
    reorder_threshold: int
    target_stock_level: int
    status: str  # computed: OK / LOW / OUT


class ProductOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    sku: str
    name: str
    brand: Optional[str] = None
    category: Optional[str] = None
    image_path: Optional[str] = None
    vendor: Optional[VendorOut] = None
    variants: List[VariantOut]
    total_stock: int


class StockAdjustment(BaseModel):
    variant_code: str
    new_count: int          # the physically-counted true value
    note: Optional[str] = "manual recount"


class OrderLineResult(BaseModel):
    sku: str
    size: Optional[str] = None
    qty_ordered: int
    qty_fulfilled: int
    remaining_stock: Optional[int] = None
    status: str
    product_name: Optional[str] = None
    note: Optional[str] = None


class OrderUploadResult(BaseModel):
    order_upload_id: int
    filename: str
    total_rows: int
    unique_skus: int
    fulfilled_count: int
    insufficient_count: int
    not_found_count: int
    lines: List[OrderLineResult]
    new_alerts: List[str]   # variant codes newly alerted as part of this upload
    file_note: Optional[str] = None   # e.g. "no quantity column - each row counted as 1"


class AlertOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    variant_code: str
    sku: str
    size: str
    product_name: str
    alert_type: str
    stock_at_alert: int
    created_at: datetime


class ReorderItemOut(BaseModel):
    sku: str
    size: str
    variant_code: str
    name: str
    current_stock: int
    required_qty: int
    vendor: Optional[str] = None
    image_path: Optional[str] = None


class TransactionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    change_qty: int
    transaction_type: str
    reference: Optional[str] = None
    balance_after: int
    created_by: str
    created_at: datetime


class VariantLookupOut(BaseModel):
    variant_code: str
    sku: str
    size: str
    product_name: str
    current_stock: int
    counted: bool = True     # False = stock never counted yet (current_stock is a placeholder 0)
    image_path: Optional[str] = None


class VariantRowOut(BaseModel):
    """One flat row per (design, size) - what the Live Inventory table shows."""
    variant_code: str
    sku: str
    size: str
    product_name: str
    brand: Optional[str] = None
    category: Optional[str] = None
    vendor: Optional[str] = None
    image_path: Optional[str] = None
    current_stock: int
    reorder_threshold: int
    target_stock_level: int
    status: str            # UNCOUNTED / OUT / LOW / OK
    last_counted_at: Optional[datetime] = None


class VariantPage(BaseModel):
    total: int
    limit: int
    offset: int
    items: List[VariantRowOut]


class SummaryOut(BaseModel):
    products: int
    variants: int
    uncounted: int
    out: int
    low: int
    ok: int
    open_alerts: int


class BulkSetResult(BaseModel):
    rows_processed: int
    updated_count: int
    not_found_count: int
    not_found_sample: List[str]
    skipped_blank_count: int
    duplicate_rows_ignored: int
