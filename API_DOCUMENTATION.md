# Tolery API Documentation

**Version:** 1.0.0 | **Updated:** January 2025

---

## 📌 Table of Contents

1. [Overview](#overview)
2. [Authentication](#authentication)
3. [Quick Start](#quick-start)
4. [API Endpoints](#api-endpoints)
5. [Edit Mode & Face Selection](#edit-mode--face-selection)
6. [PowerShell Testing Examples](#powershell-testing-examples)

---

## Overview

Tolery API allows you to generate 3D CAD models from text descriptions using AI.

### ✨ Key Features

- 🎨 **Text-to-CAD**: Generate 3D models from text descriptions
- 📄 **PDF Processing**: Extract design information from PDF files and generate CAD models
- 🖼️ **Image Processing**: Analyze images (photos, sketches) and create 3D models from visual content
- ✏️ **Edit Mode**: Modify models with face selection
- 📦 **Multiple Export Formats**: OBJ, STEP, Technical Drawing
- 🔄 **Real-time Streaming**: Track generation progress

### 🌐 Base URL

```
Production: https://dfm-api-preprod.tolery.io/api-production
```

---

## Authentication

### 🔑 API Key

```
Authorization: Bearer yJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJ1c2VyX2lkIjoxMjMsInVzZXJuYW1lIjoiaHV5Iiwicm9sZSI6ImFkbWluIn0.E_YGrdhOQcuUcQ6Ugn_wUOhoMxu-bD2Ajol5YvhJit8
```

### ⚠️ Security Notes

- ❌ Never share your API key
- ❌ Don't commit keys to Git
- ✅ Store key in environment variables (.env file)

---

## Quick Start

### Example 1: Create Simple Model

**Request (PowerShell):**

```powershell
$headers = @{
    "Authorization" = "Bearer YOUR_API_KEY"
    "Content-Type" = "application/json"
}

$body = @{
    message = "create rectangular 40x60x80"
    session_id = "my_session"
} | ConvertTo-Json

Invoke-RestMethod -Uri "https://dfm-api-preprod.tolery.io/api-production/chat_to_cad" `
    -Method Post `
    -Headers $headers `
    -Body $body
```

**Response:**

```json
{
  "chat_response": "Successfully created a rectangular box 40x60x80mm.",
  "session_id": "auto_session_123",
  "obj_export": "https://.../my_box.obj",
  "step_export": "https://.../my_box.step",
  "technical_drawing_export": "https://.../my_box.pdf"
}
```

### Example 2: Edit Model

**Step 1 - Create base model (PowerShell):**

```powershell
$headers = @{
    "Authorization" = "Bearer YOUR_API_KEY"
    "Content-Type" = "application/json"
}

$body = @{
    message = "rectangular 40x60x80"
    session_id = "my_design_v1"
} | ConvertTo-Json

Invoke-RestMethod -Uri "https://dfm-api-preprod.tolery.io/api-production/chat_to_cad" `
    -Method Post `
    -Headers $headers `
    -Body $body
```

**Step 2 - Add features (PowerShell):**

```powershell
$body = @{
    message = "Add 4 mounting holes 5mm diameter at corners"
    session_id = "my_design_v1"
    is_edit_request = $true
} | ConvertTo-Json

Invoke-RestMethod -Uri "https://dfm-api-preprod.tolery.io/api-production/chat_to_cad" `
    -Method Post `
    -Headers $headers `
    -Body $body
```

**💡 Key Point:** Use same `session_id` to maintain design context

---

## API Endpoints

### 1️⃣ Generate CAD Model

**Endpoint:** `POST /chat_to_cad`

**Description:** Generate 3D CAD models from text, or URLs with AI.

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `message` | string | ✅ | Design description (text/URL) |
| `session_id` | string | ❌ | Session ID (auto-generated if empty) |
| `is_edit_request` | boolean | ❌ | `true` when editing existing model |

**Example (PowerShell):**

```powershell
$headers = @{
    "Authorization" = "Bearer YOUR_API_KEY"
    "Content-Type" = "application/json"
}

$body = @{
    message = "rectangular 40x60x80"
    session_id = "session_001"
} | ConvertTo-Json

Invoke-RestMethod -Uri "https://dfm-api-preprod.tolery.io/api-production/chat_to_cad" `
    -Method Post `
    -Headers $headers `
    -Body $body
```

**Response:**

```json
{
  "chat_response": "AI response message",
  "session_id": "session_001",
  "obj_export": "https://.../box_v2_filleted.obj",
  "step_export": "https://.../box_v2_filleted.step",
  "technical_drawing_export": "https://.../box_v2_filleted.pdf",
  "manufacturing_errors": [],
  "attribute_and_transientid_map": {
    "face_top": "Face6",
    "face_bottom": "Face1"
  }
}
```

---

### 2️⃣ Real-time Streaming

**Endpoint:** `GET /api/generate-cad-stream`

**Description:** Generate CAD with real-time progress updates using Server-Sent Events (SSE).

**Query Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `message` | string | ✅ | Design description (URL-encoded) |
| `session_id` | string | ❌ | Session ID for context |
| `token` | string | ✅ | Authentication token |
| `is_edit_request` | boolean | ❌ | Edit mode flag |

**Example (PowerShell):**

```powershell
# Basic usage
$message = [System.Uri]::EscapeDataString("rectangular 40x60x80")
Invoke-WebRequest -Uri "https://dfm-api-preprod.tolery.io/api-production/api/generate-cad-stream?message=$message&token=YOUR_API_KEY" `
    -Headers @{"Accept" = "text/event-stream"}

# With optional parameters
$message = [System.Uri]::EscapeDataString("rectangular 40x60x80")
Invoke-WebRequest -Uri "https://dfm-api-preprod.tolery.io/api-production/api/generate-cad-stream?message=$message&session_id=my_session&is_edit_request=false&token=YOUR_API_KEY" `
    -Headers @{"Accept" = "text/event-stream"}
```

**Response Stream:**

```
data: {"step": "analysis", "status": "Analyzing request...", "overall_percentage": 10}

data: {"step": "parameters", "status": "Extracting dimensions...", "overall_percentage": 30}

data: {"step": "generation_code", "status": "Generating FreeCAD code...", "overall_percentage": 50}

data: {"step": "export", "status": "Exporting files...", "overall_percentage": 80}

data: {"step": "complete", "status": "Generation completed!", "overall_percentage": 100}

data: {"final_result": {"chat_response": "...", "obj_export": "https://...", ...}}
```

**Progress Steps:**

1. **analysis** → Analyzing request (10%)
2. **parameters** → Extracting dimensions (30%)
3. **generation_code** → Generating FreeCAD code (50%)
4. **export** → Exporting files (80%)
5. **complete** → Completed (100%)

---

### 3️⃣ Session Management

#### 📋 List All Sessions

**Endpoint:** `GET /sessions`

**Description:** Retrieve all available sessions with metadata.

**Example (PowerShell):**

```powershell
$headers = @{
    "Authorization" = "Bearer YOUR_API_KEY"
}

Invoke-RestMethod -Uri "https://dfm-api-preprod.tolery.io/api-production/sessions" `
    -Method Get `
    -Headers $headers
```

**Response:**

```json
{
  "sessions": [
    {
      "session_id": "session_001",
      "created_at": "2025-01-14T10:30:00Z",
      "updated_at": "2025-01-14T11:45:00Z",
      "message_count": 5,
      "last_message": "Add 5mm fillet to edges"
    },
    {
      "session_id": "session_002",
      "created_at": "2025-01-14T09:15:00Z",
      "updated_at": "2025-01-14T09:30:00Z",
      "message_count": 2,
      "last_message": "Create flange with 6 bolt holes"
    }
  ]
}
```

---

#### 🔍 Get Session Details

**Endpoint:** `GET /sessions/{session_id}`

**Description:** Get detailed information about specific session.

**Example (PowerShell):**

```powershell
$headers = @{
    "Authorization" = "Bearer YOUR_API_KEY"
}

Invoke-RestMethod -Uri "https://dfm-api-preprod.tolery.io/api-production/sessions/session_001" `
    -Method Get `
    -Headers $headers
```

**Response:**

```json
{
  "session_id": "session_001",
  "created_at": "2025-01-14 10:30:00.123456",
  "last_modified": "2025-01-14 11:45:00.234567",
  "document_info": {
    "document_id": "35b94c1786ce15f211fa376a",
    "workspace_id": "acc3fe5c5bb850f4e0aa6998",
    "element_id": "c07c00d1d1323b9f49bd3622",
    "folder_id": "2dd8d7ae982d4163e732db9a"
  }
}
```

**Response Fields:**
- `session_id`: Unique session identifier
- `created_at`: Session creation timestamp
- `last_modified`: Last modification timestamp (null if never modified)
- `document_info`: Document metadata (document_id, workspace_id, element_id, folder_id)

---

#### 🗑️ Delete Session

**Endpoint:** `DELETE /sessions/{session_id}`

**Description:** Delete session with soft or hard delete options.

**Query Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `delete_type` | string | "soft" | `soft` (clear data) or `hard` (remove from DB) |

**Example 1: Soft Delete (PowerShell)**

```powershell
$headers = @{
    "Authorization" = "Bearer YOUR_API_KEY"
}

Invoke-RestMethod -Uri "https://dfm-api-preprod.tolery.io/api-production/sessions/session_001?delete_type=soft" `
    -Method Delete `
    -Headers $headers
```

**Example 2: Hard Delete (PowerShell)**

```powershell
Invoke-RestMethod -Uri "https://dfm-api-preprod.tolery.io/api-production/sessions/session_001?delete_type=hard" `
    -Method Delete `
    -Headers $headers
```

**Response:**

```json
{
  "message": "Session session_001 deleted successfully (soft delete)",
  "session_id": "session_001",
  "delete_type": "soft"
}
```

**Delete Types:**
- `soft`: Clear data, keep session record
- `hard`: Permanently remove from database

---

### 4️⃣ Export Files

**Endpoint:** `GET /api/get-export`

**Description:** Retrieve download links for exported CAD files.

**Query Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `session_id` | string | ✅ | Session identifier |
| `export_format` | string | ❌ | Filter by format: `obj`, `step`, `pdf` |

**Example 1: Get All Formats (PowerShell)**

```powershell
$headers = @{
    "Authorization" = "Bearer YOUR_API_KEY"
}

Invoke-RestMethod -Uri "https://dfm-api-preprod.tolery.io/api-production/api/get-export?session_id=session_001" `
    -Method Get `
    -Headers $headers
```

**Example 2: Get Specific Format (PowerShell)**

```powershell
Invoke-RestMethod -Uri "https://dfm-api-preprod.tolery.io/api-production/api/get-export?session_id=session_001&export_format=step" `
    -Method Get `
    -Headers $headers
```

**Response:**

```json
{
  "exports": [
    {
      "session_id": "session_410fb3_198626",
      "export_format": "obj",
      "export_link": "https://dfm-api-preprod.tolery.io/download/outputs/obj/2025-12-04/box_20251204_033415.obj",
      "export_time": "2025-12-04 02:34:16.000000"
    },
    {
      "session_id": "session_410fb3_198626",
      "export_format": "step",
      "export_link": "https://dfm-api-preprod.tolery.io/download/outputs/cad/2025-12-04/box_20251204_033415.step",
      "export_time": "2025-12-04 02:34:16.000000"
    },
    {
      "session_id": "session_410fb3_198626",
      "export_format": "pdf",
      "export_link": "https://dfm-api-preprod.tolery.io/download/outputs/pdf/2025-12-04/box_20251204_033415.pdf",
      "export_time": "2025-12-04 02:34:16.000000"
    }
  ]
}
```

**Export Formats:**

| Format | Extension | Use Case |
|--------|-----------|----------|
| `obj` | .obj | 3D visualization, web viewers |
| `step` | .step | Professional CAD software |
| `pdf` | .pdf | Manufacturing documentation |

---

### 5️⃣ Process PDF

**Endpoint:** `POST /api/process-pdf`

**Description:** Upload and process a PDF file (technical drawings, specifications, etc.) to extract design information and generate 3D CAD models. The API uses AI to analyze PDF content and automatically create CAD models based on the extracted information.

**Form Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `file` | file | ✅ | PDF file to process (must have .pdf extension) |
| `user_input` | string | ❌ | Optional text prompt to guide processing or provide additional instructions |
| `session_id` | string | ❌ | Optional session ID for conversation continuity (auto-generated if not provided) |

**How It Works:**

1. **PDF Analysis**: The system extracts text, dimensions, and technical information from the PDF
2. **AI Processing**: OpenAI analyzes the content to understand design requirements
3. **CAD Generation**: Automatically generates 3D CAD models based on extracted information
4. **Export Files**: Returns download links for OBJ, STEP, and technical drawing formats

**Example (PowerShell):**

```powershell
$headers = @{
    "Authorization" = "Bearer YOUR_API_KEY"
}

$formData = @{
    file = Get-Item -Path "C:\path\to\specification.pdf"
    user_input = "Create this part with 2mm wall thickness"
    session_id = "my_design_session"
}

Invoke-RestMethod -Uri "https://dfm-api-preprod.tolery.io/api-production/api/process-pdf" `
    -Method Post `
    -Headers $headers `
    -Form $formData
```

**Response:**

```json
{
  "success": true,
  "response": "Successfully generated CAD model based on PDF analysis. The model includes all specified dimensions and features.",
  "session_id": "session_abc123",
  "obj_export": "https://.../model.obj",
  "step_export": "https://.../model.step",
  "technical_drawing_export": "https://.../model.pdf"
}
```

**Response Fields:**

| Field | Type | Description |
|-------|------|-------------|
| `success` | boolean | Whether the processing was successful |
| `response` | string | AI-generated response message or analysis result |
| `session_id` | string | Session identifier (provided or auto-generated) |
| `obj_export` | string (optional) | URL to download OBJ file |
| `step_export` | string (optional) | URL to download STEP file |
| `technical_drawing_export` | string (optional) | URL to download technical drawing PDF |

**Use Cases:**

- 📄 **Technical Drawings**: Convert 2D technical drawings to 3D CAD models
- 📋 **Specifications**: Extract design requirements from specification documents
- 🔄 **Revisions**: Update existing models based on revised PDF documents
- 📐 **Standards**: Process industry-standard technical documentation

---

### 6️⃣ Process Image

**Endpoint:** `POST /api/process-image`

**Description:** Upload and process an image file (photos, sketches, screenshots, etc.) to extract design information and generate 3D CAD models. The API uses OpenAI Vision to analyze images and automatically create CAD models based on visual content.

**Form Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `file` | file | ✅ | Image file to process (PNG, JPG, JPEG, GIF, BMP, TIFF, WEBP) |
| `user_input` | string | ❌ | Optional text prompt to guide processing or provide additional instructions |
| `session_id` | string | ❌ | Optional session ID for conversation continuity (auto-generated if not provided) |

**Supported Image Formats:**

| Format | Extensions | Notes |
|--------|------------|-------|
| **PNG** | .png | Best for screenshots and diagrams |
| **JPEG** | .jpg, .jpeg | Common photo format |
| **GIF** | .gif | Animated or static images |
| **BMP** | .bmp | Bitmap format |

**How It Works:**

1. **Image Analysis**: OpenAI Vision API analyzes the image to extract visual information
2. **Design Recognition**: Identifies dimensions, shapes, features, and design elements
3. **CAD Generation**: Automatically generates 3D CAD models based on visual analysis
4. **Export Files**: Returns download links for OBJ, STEP, and technical drawing formats

**Example (PowerShell):**

```powershell
$headers = @{
    "Authorization" = "Bearer YOUR_API_KEY"
}

$formData = @{
    file = Get-Item -Path "C:\path\to\sketch.png"
    user_input = "Make this part 50mm tall with rounded corners"
    session_id = "my_design_session"
}

Invoke-RestMethod -Uri "https://dfm-api-preprod.tolery.io/api-production/api/process-image" `
    -Method Post `
    -Headers $headers `
    -Form $formData
```

**Response:**

```json
{
  "success": true,
  "response": "Successfully generated CAD model based on image analysis. The model matches the visual design with extracted dimensions.",
  "session_id": "session_xyz789",
  "obj_export": "https://.../model.obj",
  "step_export": "https://.../model.step",
  "technical_drawing_export": "https://.../model.pdf"
}
```

**Response Fields:**

| Field | Type | Description |
|-------|------|-------------|
| `success` | boolean | Whether the processing was successful |
| `response` | string | AI-generated response message or analysis result |
| `session_id` | string | Session identifier (provided or auto-generated) |
| `obj_export` | string (optional) | URL to download OBJ file |
| `step_export` | string (optional) | URL to download STEP file |
| `technical_drawing_export` | string (optional) | URL to download technical drawing PDF |

**Use Cases:**

- 📸 **Product Photos**: Convert product photos to 3D CAD models
- ✏️ **Hand Sketches**: Transform hand-drawn sketches into 3D models
- 📐 **Technical Drawings**: Process scanned technical drawings
- 🖼️ **Screenshots**: Extract designs from screenshots or digital images
- 🔄 **Prototyping**: Quickly prototype from reference images

**Comparison: PDF vs Image Processing**

| Feature | PDF Processing | Image Processing |
|---------|---------------|------------------|
| **Best For** | Technical documents, specifications | Photos, sketches, visual references |
| **Text Extraction** | ✅ Excellent | ❌ Limited |
| **Visual Analysis** | ⚠️ Limited | ✅ Excellent |
| **Dimension Detection** | ✅ From text | ✅ From visual analysis |
| **File Types** | PDF only | Multiple image formats |

---

### 7️⃣ Chat History

**Endpoint:** `GET /api/chat-history/{session_id}`

**Description:** Retrieve complete conversation history for a session.

**Example (PowerShell):**

```powershell
$headers = @{
    "Authorization" = "Bearer YOUR_API_KEY"
}

Invoke-RestMethod -Uri "https://dfm-api-preprod.tolery.io/api-production/api/chat-history/session_001" `
    -Method Get `
    -Headers $headers
```

**Response:**

```json
{
  "session_id": "session_001",
  "messages": [
    {
      "role": "human",
      "content": "rectangular 40x60x80",
      "timestamp": "2025-01-14T10:30:00Z"
    },
    {
      "role": "ai",
      "content": "Successfully created a rectangular box with dimensions 40x60x80mm.",
      "timestamp": "2025-01-14T10:30:15Z"
    },
    {
      "role": "human",
      "content": "Add 5mm fillet to edges",
      "timestamp": "2025-01-14T10:32:00Z"
    },
    {
      "role": "ai",
      "content": "Applied 5mm fillet to all edges of the box.",
      "timestamp": "2025-01-14T10:32:20Z"
    }
  ]
}
```

---

## Edit Mode & Face Selection

### 🔍 Overview

**Edit Mode** allows you to modify existing 3D models by selecting specific faces and applying operations like extrusion, filleting, chamfering, holes, and pockets.

### 🔄 Workflow

**Step 1: Generate Initial Model**
- User sends a request to create a 3D model (e.g., "rectangular 40x60x80")
- System generates and displays the 3D model in the viewer

**Step 2: View 3D Model**
- User views the generated 3D model in the interactive viewer
- The model is displayed with all faces visible

**Step 3: Select a Face**
- User clicks on any face of the 3D model
- System opens the **Information Panel** showing details about the selected face

**Step 4: Activate Face Selection**
- In the Information Panel, user finds the **"Select Face for Chat"** button
- User clicks the button
- System automatically inserts face selection information into the chatbot text input

**Step 5: Enter Edit Command**
- User types their desired modification in the chatbot (e.g., "Add 5mm extrusion", "Create mounting hole")
- The face selection information is already included in the message

**Step 6: Apply Edit Mode**
- User enables **Edit Mode** (via checkbox or toggle)
- User sends the message
- System processes the edit request and updates the model

**Step 7: View Updated Model**
- System generates the modified 3D model
- New model is displayed in the viewer with the applied changes
- User can repeat the process for additional edits

---

### 🔧 Supported Operations

| Operation | Description | Example Command |
|-----------|-------------|-----------------|
| **Extrusion** | Extend the selected face outward or inward | "Add 10mm extrusion on selected face" |
| **Fillet** | Round the edges of the selected face | "Apply 3mm fillet to edges" |
| **Chamfer** | Bevel the edges of the selected face | "Add 2mm chamfer" |
| **Hole** | Create holes in the selected face | "Create 5mm diameter hole" |
| **Pocket** | Create recessed areas in the selected face | "Make 8mm deep pocket" |
| **Pattern** | Create multiple copies of features | "Create 4x4 pattern with 10mm spacing" |

---
