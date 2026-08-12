/**
 * PDF Viewer Manager
 * Handles PDF Technical Drawing display below chat area
 */

class PDFViewerManager {
  constructor() {
    this.container = document.getElementById('pdf-viewer-container');
    this.iframe = document.getElementById('pdf-iframe');
    this.loadingIndicator = document.getElementById('pdf-loading');
    this.emptyState = document.getElementById('pdf-empty-state');
    this.filenameDisplay = document.getElementById('pdf-filename-display');
    
    // Control buttons
    this.fullscreenBtn = document.getElementById('pdf-fullscreen-btn');
    this.downloadBtn = document.getElementById('pdf-download-btn');
    this.closeBtn = document.getElementById('pdf-close-btn');
    
    // State
    this.currentPdfUrl = null;
    this.currentPdfFilename = null;
    this.isFullscreen = false;
    
    this.initializeEventListeners();
  }
  
  /**
   * Initialize event listeners for controls
   */
  initializeEventListeners() {
    // Fullscreen button
    if (this.fullscreenBtn) {
      this.fullscreenBtn.addEventListener('click', () => this.toggleFullscreen());
    }
    
    // Download button
    if (this.downloadBtn) {
      this.downloadBtn.addEventListener('click', () => this.downloadPDF());
    }
    
    // Close button
    if (this.closeBtn) {
      this.closeBtn.addEventListener('click', () => this.hide());
    }
    
    // Listen for fullscreen changes
    document.addEventListener('fullscreenchange', () => this.handleFullscreenChange());
    document.addEventListener('webkitfullscreenchange', () => this.handleFullscreenChange());
    document.addEventListener('mozfullscreenchange', () => this.handleFullscreenChange());
    document.addEventListener('MSFullscreenChange', () => this.handleFullscreenChange());
    
    // Escape key to exit fullscreen
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && this.isFullscreen) {
        this.exitFullscreen();
      }
    });
  }
  
  /**
   * Load and display PDF from URL
   * @param {string} pdfUrl - URL to the PDF file
   * @param {string} filename - Optional filename for display
   */
  async loadPDF(pdfUrl, filename = 'Technical Drawing') {
    if (!pdfUrl) {
      console.error('No PDF URL provided');
      return;
    }
    
    console.log('📄 Loading PDF:', pdfUrl);
    
    // Store current PDF info
    this.currentPdfUrl = pdfUrl;
    this.currentPdfFilename = filename;
    
    // Update filename display
    if (this.filenameDisplay) {
      this.filenameDisplay.textContent = filename;
    }
    
    // Show container
    this.show();
    
    // Show loading state
    this.showLoading();
    
    try {
      // Normalize PDF URL
      const normalizedUrl = this.normalizePdfUrl(pdfUrl);
      console.log('🔗 Normalized PDF URL:', normalizedUrl);

      // Add PDF.js parameters for better viewing in fullscreen
      // #view=Fit - Fit entire page in window
      // #zoom=page-fit - Alternative zoom parameter
      const pdfUrlWithParams = `${normalizedUrl}#view=Fit&zoom=page-fit`;

      // Load PDF in iframe
      this.iframe.src = pdfUrlWithParams;
      
      // Wait for iframe to load
      this.iframe.onload = () => {
        this.hideLoading();
        this.container.classList.add('loaded');
        setTimeout(() => {
          this.container.classList.remove('loaded');
        }, 1500);
        console.log('✅ PDF loaded successfully');
      };
      
      this.iframe.onerror = () => {
        this.hideLoading();
        this.showError('Failed to load PDF');
        console.error('❌ Failed to load PDF');
      };
      
    } catch (error) {
      console.error('Error loading PDF:', error);
      this.hideLoading();
      this.showError('Error loading PDF: ' + error.message);
    }
  }
  
  /**
   * Normalize PDF URL to ensure proper format for viewing (not download)
   * @param {string} pdfUrl - Original PDF URL
   * @returns {string} Normalized URL for PDF viewer
   */
  normalizePdfUrl(pdfUrl) {
    // If it's a full URL with http/https, extract pathname
    if (pdfUrl.startsWith('http://') || pdfUrl.startsWith('https://')) {
      try {
        const url = new URL(pdfUrl);
        pdfUrl = url.pathname;
      } catch (error) {
        console.warn('Error parsing PDF URL:', error);
      }
    }

    // Remove /download/ prefix if present and replace with /api/pdf-viewer/
    if (pdfUrl.startsWith('/download/')) {
      pdfUrl = pdfUrl.substring('/download/'.length);
    }

    // Remove /api/download/ prefix if present
    if (pdfUrl.startsWith('/api/download/')) {
      pdfUrl = pdfUrl.substring('/api/download/'.length);
    }

    // Remove leading slash if present
    if (pdfUrl.startsWith('/')) {
      pdfUrl = pdfUrl.substring(1);
    }

    // Return URL for PDF viewer endpoint (inline viewing, not download)
    return `/api/pdf-viewer/${pdfUrl}`;
  }
  
  /**
   * Show the PDF viewer container
   */
  show() {
    if (this.container) {
      this.container.classList.add('visible');
      console.log('👁️ PDF viewer shown');
    }
  }
  
  /**
   * Hide the PDF viewer container
   */
  hide() {
    if (this.container) {
      this.container.classList.remove('visible');
      this.exitFullscreen();
      console.log('🙈 PDF viewer hidden');
    }
  }
  
  /**
   * Show loading indicator
   */
  showLoading() {
    if (this.loadingIndicator) {
      this.loadingIndicator.classList.remove('hidden');
    }
    if (this.emptyState) {
      this.emptyState.classList.add('hidden');
    }
  }
  
  /**
   * Hide loading indicator
   */
  hideLoading() {
    if (this.loadingIndicator) {
      this.loadingIndicator.classList.add('hidden');
    }
  }
  
  /**
   * Show error message
   * @param {string} message - Error message to display
   */
  showError(message) {
    if (this.emptyState) {
      this.emptyState.classList.remove('hidden');
      const titleElement = this.emptyState.querySelector('.pdf-empty-state-title');
      const subtitleElement = this.emptyState.querySelector('.pdf-empty-state-subtitle');
      
      if (titleElement) {
        titleElement.textContent = 'Error Loading PDF';
      }
      if (subtitleElement) {
        subtitleElement.textContent = message;
      }
    }
  }
  
  /**
   * Toggle fullscreen mode
   */
  toggleFullscreen() {
    if (this.isFullscreen) {
      this.exitFullscreen();
    } else {
      this.enterFullscreen();
    }
  }
  
  /**
   * Enter fullscreen mode
   */
  enterFullscreen() {
    if (!this.container) return;

    const elem = this.container;

    if (elem.requestFullscreen) {
      elem.requestFullscreen();
    } else if (elem.webkitRequestFullscreen) {
      elem.webkitRequestFullscreen();
    } else if (elem.mozRequestFullScreen) {
      elem.mozRequestFullScreen();
    } else if (elem.msRequestFullscreen) {
      elem.msRequestFullscreen();
    } else {
      // Fallback: use CSS fullscreen
      this.container.classList.add('fullscreen');
      this.isFullscreen = true;
      this.updateFullscreenButton();
    }

    // Reload iframe with fullscreen-optimized parameters after entering fullscreen
    setTimeout(() => {
      if (this.currentPdfUrl && this.isFullscreen) {
        const normalizedUrl = this.normalizePdfUrl(this.currentPdfUrl);
        // Use Fit view for fullscreen to maximize PDF size
        this.iframe.src = `${normalizedUrl}#view=Fit&zoom=page-fit&pagemode=none`;
      }
    }, 100);
  }
  
  /**
   * Exit fullscreen mode
   */
  exitFullscreen() {
    if (document.exitFullscreen) {
      document.exitFullscreen();
    } else if (document.webkitExitFullscreen) {
      document.webkitExitFullscreen();
    } else if (document.mozCancelFullScreen) {
      document.mozCancelFullScreen();
    } else if (document.msExitFullscreen) {
      document.msExitFullscreen();
    } else {
      // Fallback: remove CSS fullscreen
      this.container.classList.remove('fullscreen');
      this.isFullscreen = false;
      this.updateFullscreenButton();
    }

    // Reload iframe with normal parameters after exiting fullscreen
    setTimeout(() => {
      if (this.currentPdfUrl && !this.isFullscreen) {
        const normalizedUrl = this.normalizePdfUrl(this.currentPdfUrl);
        // Use FitH (fit width) for normal view
        this.iframe.src = `${normalizedUrl}#view=Fit&zoom=page-fit`;
      }
    }, 100);
  }
  
  /**
   * Handle fullscreen change events
   */
  handleFullscreenChange() {
    this.isFullscreen = !!(
      document.fullscreenElement ||
      document.webkitFullscreenElement ||
      document.mozFullScreenElement ||
      document.msFullscreenElement
    );
    
    if (this.isFullscreen) {
      this.container.classList.add('fullscreen');
    } else {
      this.container.classList.remove('fullscreen');
    }
    
    this.updateFullscreenButton();
  }
  
  /**
   * Update fullscreen button icon and text
   */
  updateFullscreenButton() {
    if (!this.fullscreenBtn) return;
    
    const icon = this.fullscreenBtn.querySelector('i');
    const text = this.fullscreenBtn.querySelector('span');
    
    if (this.isFullscreen) {
      if (icon) icon.className = 'fas fa-compress';
      if (text) text.textContent = 'Exit Fullscreen';
    } else {
      if (icon) icon.className = 'fas fa-expand';
      if (text) text.textContent = 'Fullscreen';
    }
  }
  
  /**
   * Download the current PDF
   */
  async downloadPDF() {
    if (!this.currentPdfUrl) {
      console.error('No PDF to download');
      return;
    }

    try {
      console.log('📥 Downloading PDF:', this.currentPdfUrl);

      // For download, use /download/ endpoint (not /api/pdf-viewer/)
      let downloadUrl = this.currentPdfUrl;

      // Normalize to /download/ endpoint
      if (downloadUrl.startsWith('http://') || downloadUrl.startsWith('https://')) {
        try {
          const url = new URL(downloadUrl);
          downloadUrl = url.pathname;
        } catch (error) {
          console.warn('Error parsing PDF URL:', error);
        }
      }

      // Ensure it uses /download/ prefix
      if (!downloadUrl.startsWith('/download/')) {
        // Remove any existing prefix
        downloadUrl = downloadUrl.replace(/^\/(api\/)?pdf-viewer\//, '');
        downloadUrl = downloadUrl.replace(/^\//, '');
        downloadUrl = `/download/${downloadUrl}`;
      }

      console.log('🔗 Download URL:', downloadUrl);

      // Fetch the PDF
      const response = await fetch(downloadUrl);
      if (!response.ok) {
        throw new Error(`HTTP error! status: ${response.status}`);
      }

      // Get blob
      const blob = await response.blob();

      // Create download link
      const blobUrl = window.URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = blobUrl;
      link.download = this.currentPdfFilename || 'technical-drawing.pdf';
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);

      // Clean up
      window.URL.revokeObjectURL(blobUrl);

      console.log('✅ PDF downloaded successfully');

      // Show notification if available
      if (typeof showNotification === 'function') {
        showNotification('PDF downloaded successfully!', 'success', 3000);
      }

    } catch (error) {
      console.error('Error downloading PDF:', error);
      if (typeof showNotification === 'function') {
        showNotification('Failed to download PDF: ' + error.message, 'error', 5000);
      }
    }
  }
  
  /**
   * Reset the PDF viewer
   */
  reset() {
    this.currentPdfUrl = null;
    this.currentPdfFilename = null;
    this.iframe.src = '';
    this.hide();
    this.exitFullscreen();
    console.log('🔄 PDF viewer reset');
  }
}

// Initialize PDF viewer when DOM is ready
let pdfViewer;
document.addEventListener('DOMContentLoaded', () => {
  pdfViewer = new PDFViewerManager();
  window.pdfViewer = pdfViewer; // Make it globally accessible
  console.log('✅ PDF Viewer Manager initialized');
});

