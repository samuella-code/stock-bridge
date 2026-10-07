# Business activity notifications and images

Migration `0015_notifications_business_logo` follows `0014_social_identity`.
It adds only empty notification/preference tables and a nullable `business.logo_key`.
Existing records, entitlements, payment history and provider identities are untouched.
Do not run this migration on production as part of Task #10.

## Activity notifications

Each notification stores its owner/business, kind, title/body, optional resource ID,
unique originating event key, read timestamp and creation timestamp. Retrieval indexes
cover owner/business/read status and newest-first business history. A bell links to a
paginated active-business history; badges cap at `99+`. Read and preference mutations
are authenticated, CSRF protected and scoped to the selected accessible business.

Product creation (including Quick Add/import) adds one notification per new product.
A completed sale adds one sale notification regardless of item count. A restock batch
adds one restock notification regardless of its internal receipt/movement count.
Rows share the operation's existing transaction: no halfway commit and no network
work. A rolled-back business operation rolls back its notifications too. Existing
import receipt replay checks run before notification creation.

Stock transitions use the locked pre-operation quantity, current minimum threshold,
and resulting quantity. Positive → zero creates **out of stock**, taking precedence
over low stock. Above threshold → positive at/below threshold creates **low stock**.
Already-low → still-low and zero → zero create no new stock alert. Replenishment above
the threshold automatically permits a later crossing alert; positive replenishment
permits a later zero alert. No separate alert-state counter is maintained. Sales and
adjustments use this logic; restocks/voids increase actual quantity, which resets
eligibility naturally. Opening inventory alone is not a downward crossing.

In-app operational notifications always remain available. The preference table stores
business-scoped low/out-of-stock email opt-ins for future support, default OFF. The UI
explicitly says email delivery is not enabled. No operational or marketing emails are
sent by this feature; existing billing/email infrastructure is unchanged.

## Image validation and private storage

Uploads accept JPG/JPEG, PNG and WebP, up to **1 MiB**, **12 megapixels**, non-animated.
Both extension and decoded format must agree. Pillow verifies then loads the image;
pixel re-encoding strips supplied metadata/appended content, normalizes orientation,
and resizes to at most 512×512 WebP. Server-generated business-prefixed random keys
ignore uploaded filenames. No image bytes are stored in SQL.

Image GET/upload/remove endpoints verify authenticated ownership, email verification,
business accessibility and suspension through the existing application gates. Upload
and removal also require write entitlement and CSRF. Private image responses are
`no-store` and `nosniff`; no public bucket URL or arbitrary storage key endpoint exists.
Default initials are shown when there is no image. Switching changes the active
business image without changing entitlement or business-selection rules.

The profile page never contacts the image provider to render its upload availability.
Storage is used only on logo upload/read/removal. No public homepage dependency is added.

### Configuration

Default: `BUSINESS_IMAGE_STORAGE=disabled`. The UI honestly disables uploads.

Development/tests only:

```
BUSINESS_IMAGE_STORAGE=local
BUSINESS_IMAGE_LOCAL_DIR=/absolute/private/development/image-directory
```

Do not serve that directory as static files. Local mode is refused when `VERCEL` is set
or `FLASK_ENV=production`; Vercel's temporary disk is not persistent customer storage.

Hosted staging/production requires a private S3-compatible bucket and:

```
BUSINESS_IMAGE_STORAGE=s3
BUSINESS_IMAGE_S3_BUCKET=<private bucket name>
BUSINESS_IMAGE_S3_REGION=<region; default us-east-1>
BUSINESS_IMAGE_S3_ENDPOINT=<HTTPS endpoint for compatible providers; omit for AWS>
BUSINESS_IMAGE_S3_ACCESS_KEY_ID=<set securely, never commit>
BUSINESS_IMAGE_S3_SECRET_ACCESS_KEY=<set securely, never commit>
```

AWS deployments with a securely configured IAM role may use the SDK credential chain
instead of explicit keys. For Vercel, supply scoped credentials securely unless a
trusted role mechanism has been independently configured. Keep bucket public access
blocked. Limit credentials to PutObject/GetObject/DeleteObject on `businesses/*`;
enable encryption at rest, monitor failures and maintain storage backups/lifecycle.
This implementation adds no provider account or credentials and changes no hosting
environment. Pillow handles validation; boto3 is the standard S3 SDK with bounded
connection/read timeouts and one retry. Neither package adds browser JavaScript.

### Replacement and cleanup reliability

Upload to a fresh key first, then lock the business and atomically commit its reference.
If storage or SQL fails, preserve the old reference and attempt new-object cleanup.
Only after a successful reference commit is the previous object deleted. Removal
commits the null reference before cleanup. Cleanup failure does not roll back a valid
business operation; logs are generic and exclude provider exceptions/credentials.

SQL and object storage cannot share a transaction. Process interruption after upload,
or cleanup failure, can leave an unreferenced private object. Operators must reconcile
unreferenced keys after a retention window, using a separately reviewed read-only
inventory and deletion procedure. No broad automatic deletion job is introduced.
Business/account deletion similarly requires object cleanup; database cascades remove
the new notification records. No existing customer reset/deletion was executed.

## Verification limits

Automated fixtures use disposable SQLite and temporary local storage. S3 tests stub
the SDK and do not claim a real provider upload. Actual hosted image acceptance needs
the private provider configured, this feature branch deployed to isolated staging and
the staging migration applied. Real responsive viewport PASS requires a rendered
browser at each requested size; CSS inspection/unit assertions alone are insufficient.
