/**
 * Download Manager
 * Handles file downloads for STEP, OBJ, JSON, and PDF files
 */

class DownloadManager {
  constructor() {
    this.downloadPanel = document.getElementById('download-panel');
    this.downloadAllBtn = document.getElementById('download-all-btn');
    this.availableFilesCount = document.getElementById('available-files-count');
    
    // Download buttons
    this.downloadButtons = {
      step: document.getElementById('download-step-btn'),
      obj: document.getElementById('download-obj-btn'),
      json: document.getElementById('download-json-btn'),
      pdf: document.getElementById('download-pdf-btn')
    };
    
    // File paths storage
    this.filePaths = {
      step: null,
      obj: null,
      json: null,
      pdf: null
    };
    
    this.initializeEventListeners();
  }
  
  /**
   * Initialize event listeners for download buttons
   */
  initializeEventListeners() {
    // Individual download buttons
    Object.keys(this.downloadButtons).forEach(fileType => {
      const btn = this.downloadButtons[fileType];
      if (btn) {
        btn.addEventListener('click', () => this.downloadFile(fileType));
      }
    });
    
    // Download all button
    if (this.downloadAllBtn) {
      this.downloadAllBtn.addEventListener('click', () => this.downloadAllFiles());
    }
  }
  
  /**
   * Update download panel with file information from API response
   * @param {Object} response - API response containing file paths
   */
  updateFromResponse(response) {
    console.log('📥 Updating download panel from response:', response);
    
    // Extract file paths from response
    const filePaths = {
      step: response.step_export || response.step_path,
      obj: response.obj_export || response.obj_path,
      json: response.json_export || response.json_path,
      pdf: response.technical_drawing_export || response.technical_drawing_path
    };
    
    // Update each file
    let availableCount = 0;
    Object.keys(filePaths).forEach(fileType => {
      if (filePaths[fileType]) {
        this.updateFileButton(fileType, filePaths[fileType]);
        availableCount++;
      }
    });
    
    // Update available files count
    if (this.availableFilesCount) {
      this.availableFilesCount.textContent = availableCount;
    }
    
    // Show/hide panel based on available files
    if (availableCount > 0) {
      this.showPanel();
      this.updateDownloadAllButton(availableCount);
    } else {
      this.hidePanel();
    }
  }
  
  /**
   * Update individual file button
   * @param {string} fileType - Type of file (step, obj, json, pdf)
   * @param {string} filePath - Path to the file
   */
  updateFileButton(fileType, filePath) {
    const btn = this.downloadButtons[fileType];
    if (!btn) return;
    
    // Store file path
    this.filePaths[fileType] = filePath;
    
    // Enable button
    btn.classList.remove('disabled');
    btn.classList.add('ready');
    
    // Update status badge
    const statusBadge = btn.querySelector('.download-status');
    if (statusBadge) {
      statusBadge.textContent = 'Ready';
      statusBadge.classList.remove('unavailable');
      statusBadge.classList.add('ready');
    }

    // Update file size display to "Ready" instead of fetching
    // (Fetching file size causes CORS errors)
    const sizeElement = document.getElementById(`${fileType}-file-size`);
    if (sizeElement) {
      sizeElement.textContent = 'Ready to download';
    }

    console.log(`✅ ${fileType.toUpperCase()} file ready: ${filePath}`);
  }
  
  /**
   * Update file size display
   * @param {string} fileType - Type of file
   * @param {string} filePath - Path to the file
   */
  async updateFileSize(fileType, filePath) {
    const sizeElement = document.getElementById(`${fileType}-file-size`);
    if (!sizeElement) return;

    // Disabled file size fetching to prevent CORS errors
    // Just show "Ready to download"
    sizeElement.textContent = 'Ready';
    return;

    /* Original code - disabled due to CORS issues
    try {
      // Try to get file size from server
      const response = await fetch(filePath, { method: 'HEAD' });
      const contentLength = response.headers.get('content-length');

      if (contentLength) {
        const sizeInBytes = parseInt(contentLength);
        const sizeFormatted = this.formatFileSize(sizeInBytes);
        sizeElement.textContent = sizeFormatted;
      } else {
        sizeElement.textContent = 'Available';
      }
    } catch (error) {
      console.warn(`Could not fetch file size for ${fileType}:`, error);
      sizeElement.textContent = 'Available';
    }
    */
  }
  
  /**
   * Format file size in human-readable format
   * @param {number} bytes - File size in bytes
   * @returns {string} Formatted file size
   */
  formatFileSize(bytes) {
    if (bytes === 0) return '0 Bytes';
    
    const k = 1024;
    const sizes = ['Bytes', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    
    return Math.round((bytes / Math.pow(k, i)) * 100) / 100 + ' ' + sizes[i];
  }
  
  /**
   * Download a single file
   * @param {string} fileType - Type of file to download
   */
  async downloadFile(fileType) {
    const filePath = this.filePaths[fileType];
    if (!filePath) {
      console.error(`No file path for ${fileType}`);
      return;
    }

    const btn = this.downloadButtons[fileType];
    if (!btn || btn.classList.contains('disabled')) return;

    // Show loading state
    btn.classList.add('loading');

    try {
      console.log(`📥 Downloading ${fileType.toUpperCase()} file: ${filePath}`);

      // Normalize the file path to ensure it's a proper download URL
      let downloadUrl = this.normalizeDownloadUrl(filePath);
      console.log(`🔗 Normalized download URL: ${downloadUrl}`);

      // Use fetch + blob approach for safer downloads
      const headers = {};
      if (window.apiToken) {
        headers['Authorization'] = `Bearer ${window.apiToken}`;
      }

      const response = await fetch(downloadUrl, {
        method: 'GET',
        headers: headers
      });

      if (!response.ok) {
        throw new Error(`HTTP error! status: ${response.status}`);
      }

      // Get the blob from response
      const blob = await response.blob();

      // Create blob URL and download
      const blobUrl = window.URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = blobUrl;
      link.download = this.getFileName(filePath);
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);

      // Clean up blob URL
      window.URL.revokeObjectURL(blobUrl);

      // Show success notification
      this.showNotification(`${fileType.toUpperCase()} file downloaded successfully!`, 'success');

    } catch (error) {
      console.error(`Error downloading ${fileType} file:`, error);
      this.showNotification(`Failed to download ${fileType.toUpperCase()} file: ${error.message}`, 'error');
    } finally {
      // Remove loading state
      btn.classList.remove('loading');
    }
  }

  /**
   * Normalize download URL to ensure it's a proper relative path
   * This prevents "Dangerous site" warnings by always using same-origin requests
   * @param {string} filePath - Original file path from API
   * @returns {string} Normalized download URL (always relative path)
   */
  normalizeDownloadUrl(filePath) {
    console.log(`🔧 Normalizing file path: ${filePath}`);

    // If it's a full URL with http/https, extract the pathname only
    if (filePath.startsWith('http://') || filePath.startsWith('https://')) {
      try {
        const url = new URL(filePath);
        const pathPart = url.pathname;
        console.log(`📍 Extracted pathname from full URL: ${pathPart}`);

        // Return the pathname as-is (it should start with /download/)
        return pathPart;
      } catch (error) {
        console.warn('⚠️ Error parsing URL, treating as relative path:', error);
        // Fallback: try to extract path after domain
        const match = filePath.match(/https?:\/\/[^\/]+(.+)/);
        if (match) {
          return match[1];
        }
      }
    }

    // If it's already a relative path starting with /download/
    if (filePath.startsWith('/download/')) {
      console.log(`✅ Already in correct format: ${filePath}`);
      return filePath;
    }

    // If it starts with /api/download/
    if (filePath.startsWith('/api/download/')) {
      console.log(`✅ API download format: ${filePath}`);
      return filePath;
    }

    // If it starts with /api/ (other API endpoints)
    if (filePath.startsWith('/api/')) {
      console.log(`✅ Other API path: ${filePath}`);
      return filePath;
    }

    // If it starts with / but not /download/
    if (filePath.startsWith('/')) {
      // Assume it's a path like /outputs/obj/file.obj
      const normalized = `/download${filePath}`;
      console.log(`🔧 Added /download prefix: ${normalized}`);
      return normalized;
    }

    // Relative path without leading slash (e.g., outputs/obj/file.obj)
    const normalized = `/download/${filePath}`;
    console.log(`🔧 Constructed download path: ${normalized}`);
    return normalized;
  }
  
  /**
   * Download all available files
   */
  async downloadAllFiles() {
    if (this.downloadAllBtn.disabled) return;
    
    // Show loading state
    this.downloadAllBtn.classList.add('loading');
    this.downloadAllBtn.disabled = true;
    
    try {
      console.log('📥 Downloading all files...');
      
      // Download each available file with a small delay
      const downloadPromises = [];
      Object.keys(this.filePaths).forEach((fileType, index) => {
        if (this.filePaths[fileType]) {
          // Add delay to prevent browser blocking multiple downloads
          setTimeout(() => {
            this.downloadFile(fileType);
          }, index * 500);
        }
      });
      
      // Show success notification
      this.showNotification('All files are being downloaded!', 'success');
      
    } catch (error) {
      console.error('Error downloading all files:', error);
      this.showNotification('Failed to download all files', 'error');
    } finally {
      // Remove loading state after a delay
      setTimeout(() => {
        this.downloadAllBtn.classList.remove('loading');
        this.downloadAllBtn.disabled = false;
      }, 2000);
    }
  }
  
  /**
   * Update download all button state
   * @param {number} availableCount - Number of available files
   */
  updateDownloadAllButton(availableCount) {
    if (!this.downloadAllBtn) return;
    
    if (availableCount > 0) {
      this.downloadAllBtn.disabled = false;
      const btnText = this.downloadAllBtn.querySelector('span');
      if (btnText) {
        btnText.textContent = `Download All Files (${availableCount})`;
      }
    } else {
      this.downloadAllBtn.disabled = true;
    }
  }
  
  /**
   * Extract filename from file path
   * @param {string} filePath - Full file path
   * @returns {string} Filename
   */
  getFileName(filePath) {
    return filePath.split('/').pop() || 'download';
  }
  
  /**
   * Show download panel with animation
   */
  showPanel() {
    if (this.downloadPanel) {
      this.downloadPanel.classList.remove('hidden');
      console.log('✅ Download panel shown');
    }
  }
  
  /**
   * Hide download panel
   */
  hidePanel() {
    if (this.downloadPanel) {
      this.downloadPanel.classList.add('hidden');
      console.log('❌ Download panel hidden');
    }
  }
  
  /**
   * Reset download panel to initial state
   */
  reset() {
    // Reset file paths
    this.filePaths = {
      step: null,
      obj: null,
      json: null,
      pdf: null
    };
    
    // Reset all buttons
    Object.keys(this.downloadButtons).forEach(fileType => {
      const btn = this.downloadButtons[fileType];
      if (btn) {
        btn.classList.add('disabled');
        btn.classList.remove('ready', 'loading');
        
        const statusBadge = btn.querySelector('.download-status');
        if (statusBadge) {
          statusBadge.textContent = 'Unavailable';
          statusBadge.classList.remove('ready');
          statusBadge.classList.add('unavailable');
        }
        
        const sizeElement = document.getElementById(`${fileType}-file-size`);
        if (sizeElement) {
          sizeElement.textContent = '-';
        }
      }
    });
    
    // Reset available count
    if (this.availableFilesCount) {
      this.availableFilesCount.textContent = '0';
    }
    
    // Disable download all button
    if (this.downloadAllBtn) {
      this.downloadAllBtn.disabled = true;
      const btnText = this.downloadAllBtn.querySelector('span');
      if (btnText) {
        btnText.textContent = 'Download All Files';
      }
    }
    
    // Hide panel
    this.hidePanel();
    
    console.log('🔄 Download panel reset');
  }
  
  /**
   * Show notification
   * @param {string} message - Notification message
   * @param {string} type - Notification type (success, error, info)
   */
  showNotification(message, type = 'info') {
    // Use existing notification system if available
    if (typeof showNotification === 'function') {
      showNotification(message, type, 3000);
    } else {
      console.log(`[${type.toUpperCase()}] ${message}`);
    }
  }
}

// Initialize download manager when DOM is ready
let downloadManager;
document.addEventListener('DOMContentLoaded', () => {
  downloadManager = new DownloadManager();
  window.downloadManager = downloadManager; // Make it globally accessible
  console.log('✅ Download Manager initialized');
});

