document.addEventListener("DOMContentLoaded", function () {
  console.log("DOM Content Loaded - Starting initialization");

  // Initialize suggestions as hidden by default
  document.body.classList.add('suggestions-hidden');

  // Main chat elements
  const chatForm = document.getElementById("chat-form");
  const userInput = document.getElementById("user-input");
  const priorityInput = document.getElementById("priority-input");
  const chatMessages = document.getElementById("chat-messages");
  const sendBtn = document.getElementById("send-btn");
  const refreshBtn = document.getElementById("refresh-btn");
  const attachFileBtn = document.getElementById("attach-file-btn");
  const selectedFileInfo = document.getElementById("selected-file-info");
  const charCount = document.getElementById("char-count");

  // Mode and session elements
  const editModeToggle = document.getElementById("edit-mode-toggle");
  const editModeIndicator = document.getElementById("edit-mode-indicator");
  const sessionIndicator = document.getElementById("session-indicator");
  const sessionIdDisplay = document.getElementById("session-id-display");

  // Results and progress elements
  const codeOutput = document.getElementById("code-output");
  const loadObjBtn = document.getElementById("load-obj-btn");
  const objFileInput = document.getElementById("obj-file-input");
  const processingProgressSection = document.getElementById(
    "processing-progress-section"
  );
  const overallProgressPercentage = document.getElementById(
    "overall-progress-percentage"
  );
  const overallProgressBar = document.getElementById("overall-progress-bar");
  const overallProgressText = document.getElementById("overall-progress-text");

  // Legacy compatibility elements
  const chatToggle = document.getElementById("chat-toggle");
  const chatbotWidget = document.getElementById("chatbot-widget");
  const chatHistory = document.getElementById("chat-history");

  console.log("Elements found:", {
    chatForm: !!chatForm,
    userInput: !!userInput,
    chatMessages: !!chatMessages,
    sendBtn: !!sendBtn,
    refreshBtn: !!refreshBtn,
    attachFileBtn: !!attachFileBtn,
    selectedFileInfo: !!selectedFileInfo,
    charCount: !!charCount,
  });

  const progressSteps = {
    analysis: {
      icon: document.getElementById("step-icon-analysis"),
      status: document.getElementById("step-status-analysis"),
    },
    parameters: {
      icon: document.getElementById("step-icon-parameters"),
      status: document.getElementById("step-status-parameters"),
    },
    generation_code: {
      icon: document.getElementById("step-icon-generation_code"),
      status: document.getElementById("step-status-generation_code"),
    },
    export: {
      icon: document.getElementById("step-icon-export"),
      status: document.getElementById("step-status-export"),
      eta: document.getElementById("step-eta-export"),
    },
    complete: {
      icon: document.getElementById("step-icon-complete"),
      status: document.getElementById("step-status-complete"),
    },
  };
  const totalSteps = Object.keys(progressSteps).length;
  let eventSource = null; // For Server-Sent Events

  // Variable to store the latest generated code and selected files
  let latestCode = null;
  let isEditMode = false;
  let selectedPdfFile = null; // Store the selected PDF file
  let selectedImageFile = null; // Store the selected image file
  let currentSessionId = null; // Store the current session ID for conversation continuity
  let suggestionsVisible = false; // Track suggestions visibility state
  let isStreaming = false; // ✅ Global flag: true when chatbot is processing stream
  let awaitingConfirmReply = false;

  // Initialize 3D Model Viewer - Use DualViewerManager instead
  let modelViewer = null;

  // Wait for DualViewerManager to initialize both viewers
  function waitForViewers() {
    return new Promise((resolve) => {
      const checkViewers = () => {
        if (window.dualViewer && window.dualViewer.isInitialized) {
          modelViewer = window.dualViewer.objViewer;
          window.modelViewer = modelViewer; // For backward compatibility
          console.log("✅ Using DualViewerManager - Both viewers available");
          resolve();
        } else {
          setTimeout(checkViewers, 100); // Check every 100ms
        }
      };
      checkViewers();
    });
  }

  // Initialize viewers when DOM is ready
  document.addEventListener('DOMContentLoaded', () => {
    waitForViewers().then(() => {
      console.log("✅ All viewers initialized and ready");
    });
  });

  // Initialize markdown renderer
  if (typeof marked !== "undefined") {
    marked.setOptions({
      highlight: function (code, lang) {
        if (typeof hljs !== "undefined" && lang && hljs.getLanguage(lang)) {
          try {
            return hljs.highlight(code, { language: lang }).value;
          } catch (err) { }
        }
        return code;
      },
      breaks: true,
      gfm: true,
    });
    console.log("Markdown renderer initialized");
  } else {
    console.warn("Marked library not loaded");
  }

  // Initialize chat interface
  initializeChatInterface();

  console.log("Chat interface initialized. Elements found:", {
    chatForm: !!chatForm,
    userInput: !!userInput,
    chatMessages: !!chatMessages,
    sendBtn: !!sendBtn,
    refreshBtn: !!refreshBtn,
  });

  // Function to hide welcome message with animation
  function hideWelcomeMessage() {
    const welcomeMessage = chatMessages.querySelector(".chat-bubble.system");
    if (welcomeMessage) {
      const welcomeContainer = welcomeMessage.closest(".flex.justify-center");
      if (welcomeContainer) {
        welcomeContainer.style.transition =
          "opacity 0.3s ease, transform 0.3s ease";
        welcomeContainer.style.opacity = "0";
        welcomeContainer.style.transform = "translateY(-10px)";
        setTimeout(() => {
          welcomeContainer.remove();
        }, 300);
      }
    }
  }

  // Auto-resize textarea
  if (userInput) {
    userInput.addEventListener("input", function () {
      this.style.height = "auto";
      this.style.height = Math.min(this.scrollHeight, 128) + "px";

      // Update character count
      if (charCount) {
        charCount.textContent = this.value.length;
      }

      // Enable/disable send button
      if (sendBtn) {
        const shouldDisable =
          this.value.trim().length === 0 &&
          !selectedPdfFile &&
          !selectedImageFile;
        sendBtn.disabled = shouldDisable;
        console.log(
          "Send button disabled:",
          shouldDisable,
          "Text length:",
          this.value.trim().length
        );
      }
    });

    // Initial enable/disable check
    if (sendBtn) {
      const shouldDisable = !selectedPdfFile && !selectedImageFile;
      sendBtn.disabled = shouldDisable;
    }
  }

  // ✅ Lock/Unlock UI when chatbot is processing
  function setUILocked(locked) {
    isStreaming = locked;

    // Only disable the send button — Tailwind disabled:opacity-50 handles the dimming
    if (sendBtn) {
      sendBtn.disabled = locked;
    }

    // Show/hide the waiting text
    const waitingText = document.getElementById('streaming-waiting-text');
    if (waitingText) {
      waitingText.classList.toggle('hidden', !locked);
    }

    console.log(`🔒 [UI LOCK] ${locked ? 'LOCKED' : 'UNLOCKED'}`);
  }

  function isConfirmPromptText(text) {
    const normalized = String(text || "").toLowerCase();
    return (
      normalized.includes("reply yes/ok") ||
      normalized.includes("répondez oui/ok") ||
      normalized.includes("repondez oui/ok") ||
      normalized.includes("awaiting description confirmation") ||
      normalized.includes("pour générer le fichier cao") ||
      normalized.includes("to generate")
    );
  }

  function isShortConfirmReply(text) {
    const normalized = String(text || "").trim().toLowerCase();
    return /^(yes|y|ok|okay|oui|o|confirm|confirmed|go|go ahead|generate|générer|generer)$/.test(normalized);
  }

  function markAwaitingConfirmReply(enabled, reason = "") {
    awaitingConfirmReply = enabled;
    console.log(`[CONFIRM_UI] awaitingConfirmReply=${enabled}${reason ? ` | ${reason}` : ""}`);
    if (enabled) {
      setUILocked(false);
      if (sendBtn && userInput) {
        sendBtn.disabled = userInput.value.trim().length === 0;
      }
      if (userInput) {
        userInput.focus();
      }
    }
  }

  function getSelectedPriority() {
    if (window.getCadPriorityForSSE) {
      return window.getCadPriorityForSSE();
    }

    if (!priorityInput) return 0;

    const parsed = Number.parseInt(priorityInput.value, 10);
    const priority = Number.isFinite(parsed) ? Math.min(100, Math.max(0, parsed)) : 0;
    priorityInput.value = priority.toString();
    return priority;
  }

  if (priorityInput) {
    priorityInput.addEventListener("change", getSelectedPriority);
    priorityInput.addEventListener("blur", getSelectedPriority);
  }

  // Function to initialize chat interface
  function initializeChatInterface() {
    // Show progress section by default
    if (processingProgressSection) {
      processingProgressSection.classList.remove("hidden");
    }

    // Add welcome message if chat is empty
    if (chatMessages && chatMessages.children.length <= 1) {
      // Keep only the welcome message
    }
  }

  // Function to filter out suggestions from content
  function filterSuggestions(content) {
    if (typeof content !== 'string') return content;

    // If suggestions are visible, don't filter
    if (suggestionsVisible) return content;

    // Remove lines containing suggestion indicators
    const lines = content.split('\n');
    const filteredLines = lines.filter(line => {
      // Remove lines with decorative suggestion indicators
      if (line.includes('💡')) return false;
      // Only filter "Suggestions:" (plural) - keep "Suggestion:" (singular) as it's actual content
      if (line.toLowerCase().includes('suggestions:')) return false;
      // IMPORTANT: Do NOT filter "Suggestion:" (singular) - these are manufacturing recommendations!
      return true;
    });

    return filteredLines.join('\n').trim();
  }

  // Function to toggle suggestions visibility
  function toggleSuggestions() {
    suggestionsVisible = !suggestionsVisible;
    const toggleBtn = document.getElementById('suggestions-toggle');
    const toggleText = document.getElementById('suggestions-toggle-text');
    const body = document.body;

    if (toggleBtn && toggleText) {
      if (suggestionsVisible) {
        toggleText.textContent = 'Hide Tips';
        toggleBtn.classList.remove('bg-blue-500', 'hover:bg-blue-600');
        toggleBtn.classList.add('bg-orange-500', 'hover:bg-orange-600');
        body.classList.add('suggestions-visible');
        body.classList.remove('suggestions-hidden');
      } else {
        toggleText.textContent = 'Show Tips';
        toggleBtn.classList.remove('bg-orange-500', 'hover:bg-orange-600');
        toggleBtn.classList.add('bg-blue-500', 'hover:bg-blue-600');
        body.classList.add('suggestions-hidden');
        body.classList.remove('suggestions-visible');
      }
    }

    // Re-render all bot messages to apply/remove filtering
    refreshChatMessages();
  }

  // Function to refresh chat messages with current filter settings
  function refreshChatMessages() {
    // This would require storing original messages and re-rendering
    // For now, just update the toggle state
    console.log('Suggestions visibility:', suggestionsVisible ? 'visible' : 'hidden');
  }

  // Function to add message to chat
  function addChatMessage(
    content,
    type = "user",
    timestamp = null,
    attachments = null
  ) {
    if (!chatMessages) return;

    // Hide welcome message when user starts chatting
    if (type === "user") {
      hideWelcomeMessage();
    }

    const messageDiv = document.createElement("div");
    messageDiv.className = "flex mb-4";

    if (type === "user") {
      messageDiv.classList.add("justify-end");
    } else if (type === "system") {
      messageDiv.classList.add("justify-center");
    } else {
      messageDiv.classList.add("justify-start");
    }

    const bubbleDiv = document.createElement("div");
    bubbleDiv.className = `chat-bubble ${type} p-3`;

    // Filter suggestions for bot messages
    if (type === "bot") {
      content = filterSuggestions(content);
    }

    // Create content container
    const contentContainer = document.createElement("div");

    // Add file attachments if present (for user messages)
    if (
      type === "user" &&
      attachments &&
      (attachments.image || attachments.pdf)
    ) {
      const attachmentsDiv = document.createElement("div");
      attachmentsDiv.className = "mb-3 space-y-2";

      // Add image attachment
      if (attachments.image) {
        const imageDiv = document.createElement("div");
        imageDiv.className =
          "flex items-center space-x-3 bg-green-50 border border-green-200 rounded-lg p-3";

        const imagePreview = document.createElement("img");
        imagePreview.src = attachments.image.url;
        imagePreview.alt = "Attached image";
        imagePreview.className =
          "w-12 h-12 object-cover rounded-lg border border-green-200 flex-shrink-0 cursor-pointer hover:opacity-80 transition-opacity";

        // Add click handler to show full image
        imagePreview.addEventListener("click", function () {
          window.open(attachments.image.url, "_blank");
        });

        const imageInfo = document.createElement("div");
        imageInfo.className = "min-w-0 flex-1";
        imageInfo.innerHTML = `
          <p class="text-sm font-medium text-green-900 truncate">${attachments.image.name
          }</p>
          <p class="text-xs text-green-600">${(
            attachments.image.size / 1024
          ).toFixed(1)} KB • Image</p>
        `;

        const imageIcon = document.createElement("div");
        imageIcon.className = "flex-shrink-0 text-green-600";
        imageIcon.innerHTML = '<i class="fas fa-image text-sm"></i>';

        imageDiv.appendChild(imagePreview);
        imageDiv.appendChild(imageInfo);
        imageDiv.appendChild(imageIcon);
        attachmentsDiv.appendChild(imageDiv);
      }

      // Add PDF attachment
      if (attachments.pdf) {
        const pdfDiv = document.createElement("div");
        pdfDiv.className =
          "flex items-center space-x-3 bg-red-50 border border-red-200 rounded-lg p-3";

        const pdfIcon = document.createElement("div");
        pdfIcon.className =
          "flex-shrink-0 w-12 h-12 bg-red-100 rounded-lg flex items-center justify-center";
        pdfIcon.innerHTML =
          '<i class="fas fa-file-pdf text-red-600 text-lg"></i>';

        const pdfInfo = document.createElement("div");
        pdfInfo.className = "min-w-0 flex-1";
        pdfInfo.innerHTML = `
          <p class="text-sm font-medium text-red-900 truncate">${attachments.pdf.name
          }</p>
          <p class="text-xs text-red-600">${(
            attachments.pdf.size /
            1024 /
            1024
          ).toFixed(2)} MB • PDF Document</p>
        `;

        const pdfTypeIcon = document.createElement("div");
        pdfTypeIcon.className = "flex-shrink-0 text-red-600";
        pdfTypeIcon.innerHTML = '<i class="fas fa-paperclip text-sm"></i>';

        pdfDiv.appendChild(pdfIcon);
        pdfDiv.appendChild(pdfInfo);
        pdfDiv.appendChild(pdfTypeIcon);
        attachmentsDiv.appendChild(pdfDiv);
      }

      contentContainer.appendChild(attachmentsDiv);
    }

    // Add text content
    if (content && content.trim() && content !== "📎 File attachment") {
      const textDiv = document.createElement("div");

      if (type === "bot") {
        if (typeof content === "object") {
          // Handle structured content for bot messages
          textDiv.innerHTML = formatBotMessage(content);
        } else {
          // Render markdown for bot messages
          if (typeof marked !== "undefined") {
            textDiv.innerHTML = marked.parse(content);
            // Apply syntax highlighting to code blocks
            if (typeof hljs !== "undefined") {
              textDiv.querySelectorAll("pre code").forEach((block) => {
                hljs.highlightElement(block);
              });
            }
          } else {
            textDiv.textContent = content;
          }
        }
      } else {
        // Handle line breaks for user and system messages
        textDiv.innerHTML = content.replace(/\n/g, "<br>");
      }

      contentContainer.appendChild(textDiv);
    }

    bubbleDiv.appendChild(contentContainer);
    messageDiv.appendChild(bubbleDiv);
    chatMessages.appendChild(messageDiv);

    // Scroll to bottom
    chatMessages.scrollTop = chatMessages.scrollHeight;

    return messageDiv;
  }

  // Function to format bot messages
  function formatBotMessage(content) {
    if (typeof content === "string") {
      return content;
    }

    // Handle different types of bot responses
    if (content.code) {
      return `
        <div class="mb-2">${content.message || "Generated code:"}</div>
        <pre class="bg-gray-100 p-2 rounded text-xs overflow-x-auto"><code>${content.code
        }</code></pre>
      `;
    }

    return content.message || content.toString();
  }

  // Function to show typing indicator
  function showTypingIndicator() {
    if (!chatMessages) return;

    // Remove existing typing indicator
    const existing = document.getElementById("typing-indicator");
    if (existing) existing.remove();

    const typingDiv = document.createElement("div");
    typingDiv.id = "typing-indicator";
    typingDiv.className = "flex justify-start mb-4";

    const indicator = document.createElement("div");
    indicator.className = "typing-indicator";
    indicator.innerHTML = `
      <div class="typing-dot"></div>
      <div class="typing-dot"></div>
      <div class="typing-dot"></div>
    `;

    typingDiv.appendChild(indicator);
    chatMessages.appendChild(typingDiv);
    chatMessages.scrollTop = chatMessages.scrollHeight;
  }

  // Function to hide typing indicator
  function hideTypingIndicator() {
    const indicator = document.getElementById("typing-indicator");
    if (indicator) {
      indicator.remove();
    }
  }

  // Function to show AI thinking message
  function showAIThinkingMessage() {
    if (!chatMessages) return;

    // Remove existing AI thinking message
    const existing = document.getElementById("ai-thinking-message");
    if (existing) existing.remove();

    const messageDiv = document.createElement("div");
    messageDiv.id = "ai-thinking-message";
    messageDiv.className = "flex justify-start mb-4";

    const bubbleDiv = document.createElement("div");
    bubbleDiv.className = "chat-bubble bot p-3 flex items-center space-x-3";
    bubbleDiv.style.background =
      "linear-gradient(135deg, #667eea 0%, #764ba2 100%)";
    bubbleDiv.style.color = "white";
    bubbleDiv.style.animation = "pulse 2s infinite";

    bubbleDiv.innerHTML = `
      <div class="flex-shrink-0">
        <i class="fas fa-robot text-white text-lg"></i>
      </div>
      <div class="flex flex-col">
        <span class="font-medium text-sm">AI thinking...</span>
        <div class="typing-indicator mt-1">
          <div class="typing-dot" style="background-color: grey;"></div>
          <div class="typing-dot" style="background-color: grey;"></div>
          <div class="typing-dot" style="background-color: grey;"></div>
        </div>
      </div>
    `;

    messageDiv.appendChild(bubbleDiv);
    chatMessages.appendChild(messageDiv);
    chatMessages.scrollTop = chatMessages.scrollHeight;
  }

  // Function to hide AI thinking message
  function hideAIThinkingMessage() {
    const aiThinkingMessage = document.getElementById("ai-thinking-message");
    if (aiThinkingMessage) {
      aiThinkingMessage.remove();
    }
  }

  // Function to display code in results panel
  function displayCode(code) {
    if (!codeOutput) return;

    // Clear empty state
    codeOutput.innerHTML = "";

    // Create code display
    const codeContainer = document.createElement("div");
    codeContainer.className =
      "bg-gray-900 text-green-400 p-4 rounded-lg font-mono text-sm overflow-x-auto";

    const pre = document.createElement("pre");
    const codeElement = document.createElement("code");
    codeElement.textContent = code;
    pre.appendChild(codeElement);
    codeContainer.appendChild(pre);

    codeOutput.appendChild(codeContainer);

    // Load OBJ button is always visible now, no need to show/hide
  }

  // Function to clear empty state
  function clearEmptyState() {
    if (codeOutput) {
      const emptyState = codeOutput.querySelector(
        ".flex.flex-col.items-center.justify-center"
      );
      if (emptyState) {
        emptyState.remove();
      }
    }
  }

  // Function to update file display
  function updateFileDisplay() {
    if (!selectedFileInfo) return;

    selectedFileInfo.innerHTML = "";

    if (selectedPdfFile || selectedImageFile) {
      const fileDiv = document.createElement("div");
      fileDiv.className =
        "flex items-center justify-between bg-blue-50 border border-blue-200 rounded-lg p-3";

      let fileInfo = "";
      if (selectedPdfFile && selectedImageFile) {
        fileInfo = `📄 ${selectedPdfFile.name} + 🖼️ ${selectedImageFile.name}`;
      } else if (selectedPdfFile) {
        fileInfo = `📄 ${selectedPdfFile.name}`;
      } else if (selectedImageFile) {
        fileInfo = `🖼️ ${selectedImageFile.name}`;
      }

      fileDiv.innerHTML = `
        <div class="flex items-center">
          <span class="text-sm text-blue-700">${fileInfo}</span>
        </div>
        <button onclick="clearSelectedFiles()" class="text-blue-500 hover:text-blue-700">
          <i class="fas fa-times"></i>
        </button>
      `;

      selectedFileInfo.appendChild(fileDiv);
    }
  }

  // Function to clear selected files
  window.clearSelectedFiles = function () {
    selectedPdfFile = null;
    selectedImageFile = null;
    updateFileDisplay();

    // Update send button state
    if (sendBtn && userInput) {
      sendBtn.disabled = userInput.value.trim().length === 0;
    }
  };

  // Function to update session indicator
  function updateSessionIndicator(sessionId) {
    currentSessionId = sessionId;
    if (sessionIndicator && sessionIdDisplay) {
      if (sessionId) {
        sessionIndicator.classList.remove("hidden");
        sessionIdDisplay.textContent = sessionId.substring(0, 8);
      } else {
        sessionIndicator.classList.add("hidden");
        sessionIdDisplay.textContent = "";
      }
    }
  }

  function showInlineLoading(element, message, type = "primary") {
    const loadingId = `loading-${Date.now()}`;
    const colorClasses = {
      primary: "bg-indigo-600",
      success: "bg-green-600",
      warning: "bg-yellow-600",
      danger: "bg-red-600",
    };

    const loadingIndicator = document.createElement("div");
    loadingIndicator.id = loadingId;
    loadingIndicator.className = "text-center p-4 loading-fade-in";
    loadingIndicator.innerHTML = `
      <div class="inline-flex items-center px-6 py-3 ${colorClasses[type]} text-white rounded-lg shadow-lg" style="transition: none; cursor: move; position: relative; top: -300px;">
        <div class="loading-spinner-small mr-3"></div>
        <span class="font-medium">${message}</span>
      </div>
    `;

    element.appendChild(loadingIndicator);
    return loadingId;
  }

  function hideInlineLoading(loadingId) {
    const loading = document.getElementById(loadingId);
    if (loading) {
      loading.classList.add("loading-fade-out");
      setTimeout(() => {
        loading.remove();
      }, 300);
    }
  }

  // UNIFIED FILE PROCESSING - Consolidates PDF, Image, and Multi-file processing
  function processFiles(files, additionalText = "", endpoint = "/api/chat-to-cad") {
    console.log("Processing files:", files);

    // ✅ Lock UI khi bắt đầu upload/xử lý file
    setUILocked(true);

    // Hide welcome message when user starts any interaction
    hideWelcomeMessage();

    // Show AI thinking message
    showAIThinkingMessage();

    // Create FormData object
    const formData = new FormData();

    // Handle different file types
    if (Array.isArray(files)) {
      // Multi-file processing
      files.forEach((file, index) => {
        if (file.type === 'application/pdf') {
          formData.append("pdf_file", file);
        } else if (file.type.startsWith('image/')) {
          formData.append("image_file", file);
        } else {
          formData.append(`file_${index}`, file);
        }
      });
    } else {
      // Single file processing
      const file = files;
      if (file.type === 'application/pdf') {
        formData.append("file", file);
        endpoint = "/api/process-pdf";
      } else if (file.type.startsWith('image/')) {
        formData.append("file", file);
        endpoint = "/api/process-image";
      } else {
        formData.append("file", file);
      }
    }

    // Add user input if provided
    if (additionalText) {
      formData.append("user_input", additionalText);
    }

    // Add current session_id if available for continuity
    if (currentSessionId) {
      formData.append("session_id", currentSessionId);
    }

    // Add material choice from the selector
    const materialChoice = window.getSelectedMaterial ? window.getSelectedMaterial() : 'STEEL';
    formData.append("material_choice", materialChoice);
    console.log('[MATERIAL] Sending with request:', materialChoice);

    // Create loading indicator
    const chatMessagesContainer = document.getElementById("chat-messages");
    const loadingId = showInlineLoading(chatMessagesContainer, "Processing files...");

    // Send request
    const headers = {};
    if (window.apiToken) {
      headers['Authorization'] = `Bearer ${window.apiToken}`;
    }

    return fetch(endpoint, {
      method: "POST",
      headers: headers,
      body: formData,
    })
      .then(handleAPIResponse)
      .then((data) => {
        hideInlineLoading(loadingId);
        hideAIThinkingMessage();
        // ✅ Unlock UI sau khi file được xử lý thành công
        setUILocked(false);

        if (data.session_id) {
          currentSessionId = data.session_id;
          updateSessionIndicator(currentSessionId);
        }

        if (data.response) {
          addChatMessage(data.response, "bot");
        }

        if (data.obj_content) {
          handleOBJContent(data.obj_content, data.obj_filename || "generated_model.obj");
        }

        // Update download panel with file information
        if (window.downloadManager) {
          window.downloadManager.updateFromResponse(data);
        }

        // DEBUG: Log full response to see what we received
        console.log('📦 [processFiles] Full response data:', data);
        console.log('📦 [processFiles] Available fields:', {
          obj_export: data.obj_export,
          obj_path: data.obj_path,
          step_export: data.step_export,
          step_path: data.step_path,
          technical_drawing_export: data.technical_drawing_export,
          technical_drawing_path: data.technical_drawing_path
        });

        // Load PDF viewer if technical drawing is available
        if (window.pdfViewer && (data.technical_drawing_export || data.technical_drawing_path)) {
          const pdfPath = data.technical_drawing_export || data.technical_drawing_path;
          const pdfFilename = pdfPath.split('/').pop() || 'technical-drawing.pdf';
          console.log('📄 Loading PDF viewer:', pdfPath);
          window.pdfViewer.loadPDF(pdfPath, pdfFilename);
        }

        // Load 3D viewers if OBJ/STEP files are available
        const objPath = data.obj_export || data.obj_path;
        const stepPath = data.step_export || data.step_path;

        console.log('🔍 [processFiles] Checking 3D files:', { objPath, stepPath });

        if (objPath || stepPath) {
          console.log('🎯 3D model files detected:', { objPath, stepPath });

          // Use DualViewerManager if available (preferred)
          if (window.dualViewer && typeof window.dualViewer.loadModelsProgressively === 'function') {
            console.log('✅ Using DualViewerManager for progressive loading');
            window.dualViewer.loadModelsProgressively(objPath, stepPath)
              .then(results => {
                console.log('✅ 3D models loaded successfully:', results);
              })
              .catch(error => {
                console.error('❌ Error loading 3D models:', error);
              });
          }
          // Fallback to individual viewers
          else {
            console.log('⚠️ DualViewerManager not available, using fallback loaders');

            // Load OBJ model
            if (objPath && window.objViewer && typeof window.objViewer.loadModel === 'function') {
              const objFilename = objPath.split('/').pop() || 'model.obj';
              console.log('📦 Loading OBJ model:', objPath);
              window.objViewer.loadModel(objPath, objFilename)
                .catch(error => console.error('❌ Error loading OBJ:', error));
            }

            // Load STEP model
            if (stepPath) {
              const stepViewer = window.stepViewer || window.dualViewer?.stepViewer;
              if (stepViewer && typeof stepViewer.loadModel === 'function') {
                const stepFilename = stepPath.split('/').pop() || 'model.step';
                console.log('🎯 Loading STEP model:', stepPath);
                stepViewer.loadModel(stepPath, stepFilename)
                  .catch(error => console.error('❌ Error loading STEP:', error));
              }
            }
          }
        }

        return data;
      })
      .catch((error) => {
        hideInlineLoading(loadingId);
        hideAIThinkingMessage();
        // ✅ Unlock UI khi file xử lý lỗi
        setUILocked(false);
        handleAPIError(loadingId, error);
      });
  }

  // SHARED ERROR HANDLER
  function handleAPIError(loadingId, error) {
    hideInlineLoading(loadingId);
    hideAIThinkingMessage();
    console.error("Error:", error);
    // Display error in chatbot instead of status notification
    addChatMessage(`Error: ${error.message}`, "bot");
  }

  // SHARED RESPONSE HANDLER
  function handleAPIResponse(response) {
    if (!response.ok) {
      let errorMessage;
      switch (response.status) {
        case 401:
          errorMessage = "Authentication failed. Please check your API token.";
          break;
        case 403:
          errorMessage = "Access forbidden. You don't have permission to perform this action.";
          break;
        case 404:
          errorMessage = "The requested resource was not found.";
          break;
        case 500:
          errorMessage = "Internal server error. Please try again later.";
          break;
        default:
          errorMessage = `HTTP error! status: ${response.status}`;
      }
      throw new Error(errorMessage);
    }
    return response.json();
  }

  // PDF upload and processing function - now uses unified processFiles
  function processPDF(file, additionalText = "") {
    console.log("Processing PDF:", file.name);

    // Use unified file processing with PDF-specific endpoint
    // Note: 3D viewer loading is now handled automatically by processFiles()
    return processFiles(file, additionalText, "/api/process-pdf")
      .then((data) => {
        if (data.success) {
          if (data.session_id) {
            updateSessionIndicator(data.session_id);
          }
          // Note: addChatMessage and 3D viewer loading are handled by processFiles()
        } else {
          // Error messages are also handled by processFiles() via handleAPIError
          console.error("PDF processing failed:", data.response || data.error);
        }
        return data;
      });
  }

  // Image upload and processing function - now uses unified processFiles
  function processImage(file, additionalText = "") {
    console.log("Processing Image:", file.name);

    // Use unified file processing with Image-specific endpoint
    // Note: 3D viewer loading is now handled automatically by processFiles()
    return processFiles(file, additionalText, "/api/process-image")
      .then((data) => {
        if (data.success) {
          if (data.session_id) {
            updateSessionIndicator(data.session_id);
          }
          // Note: addChatMessage and 3D viewer loading are handled by processFiles()
        } else {
          // Error messages are also handled by processFiles() via handleAPIError
          console.error("Image processing failed:", data.response || data.error);
        }
        return data;
      });
  }

  // Multi-file processing function - now uses unified processFiles
  function processMultiFile(pdfFile, imageFile, additionalText = "") {
    console.log("Processing multiple files:", {
      pdf: pdfFile?.name,
      image: imageFile?.name,
    });

    // Prepare files array for unified processing
    const files = [];
    if (pdfFile) files.push(pdfFile);
    if (imageFile) files.push(imageFile);

    // Use unified file processing with multi-file endpoint
    return processFiles(files, additionalText, "/api/process-multi-file")
      .then((data) => {
        // Display results based on what was processed
        let message = "";

        if (data.pdf_result) {
          message += `PDF Analysis: ${data.pdf_result.success
            ? data.pdf_result.message
            : "Failed - " + data.pdf_result.message
            }\n\n`;
        }

        if (data.image_result) {
          message += `Image Analysis: ${data.image_result.success
            ? data.image_result.message
            : "Failed - " + data.image_result.message
            }\n\n`;
        }

        if (data.combined_result) {
          message += `Combined Analysis: ${data.combined_result.message}`;
        }

        if (message) {
          displayMessage(message.trim(), "bot");
        } else {
          displayError("No results received from file processing");
        }

        // Check for STEP viewer integration in image results
        if (data.image_result && data.image_result.step_viewer_ready && data.image_result.step_viewer_url) {
          console.log("STEP Viewer integration detected from image processing:", data.image_result.step_viewer_url);
          // Automatically load the 3D model using step-viewer
          loadStepViewer(data.image_result.step_viewer_url);
        }

        return data;
      });
  }

  // Refresh button event listener (to reset state)
  refreshBtn.addEventListener("click", function () {
    console.log(
      "%c🔄 CLIENT: Refresh button pressed",
      "color: blue; font-weight: bold"
    );

    // Show loading state on refresh button
    const originalContent = refreshBtn.innerHTML;
    refreshBtn.disabled = true;
    refreshBtn.innerHTML = '<i class="fas fa-spinner fa-spin text-sm"></i>';

    // Clear session
    updateSessionIndicator(null);

    // Clear code output area
    codeOutput.innerHTML = `
      <div class="flex flex-col items-center justify-center h-full text-gray-400">
        <i class="fas fa-robot text-4xl mb-4 text-gray-300"></i>
        <p class="text-center">Enter a description or upload an image to start generating CAD models</p>
        <p class="text-xs text-gray-400 mt-2">Supports: Text description, images (JPG, PNG), PDF files</p>
      </div>
    `;

    // Hide STEP viewer button
    viewStepBtn.classList.add("hidden");

    // Reset other state
    latestCode = null;
    selectedPdfFile = null;
    selectedImageFile = null;
    updateFileDisplay();

    // Reset download panel
    if (window.downloadManager) {
      window.downloadManager.reset();
    }

    // Call the refresh API
    const headers = {
      "Content-Type": "application/json",
    };
    if (window.apiToken) {
      headers['Authorization'] = `Bearer ${window.apiToken}`;
    }
    fetch("/api/refresh_chat", {
      method: "POST",
      headers: headers,
    })
      .then((response) => {
        if (!response.ok) {
          console.error(
            "%c❌ CLIENT: Server responded with error status: " +
            response.status,
            "color: red; font-weight: bold"
          );
          throw new Error(`Server responded with status: ${response.status}`);
        }
        return response.json();
      })
      .then((data) => {
        console.log(
          "%c✅ CLIENT: Chat refreshed successfully",
          "color: green; font-weight: bold"
        );
        console.log("Response data:", data);

        // Show success state briefly
        refreshBtn.innerHTML = '<i class="fas fa-check text-sm"></i>';
        setTimeout(() => {
          refreshBtn.innerHTML = originalContent;
          refreshBtn.disabled = false;
        }, 1000);
      })
      .catch((error) => {
        console.error(
          "%c❌ CLIENT: Error refreshing chat: " + error,
          "color: red; font-weight: bold"
        );

        // Show error state briefly
        refreshBtn.innerHTML =
          '<i class="fas fa-exclamation-triangle text-sm"></i>';
        setTimeout(() => {
          refreshBtn.innerHTML = originalContent;
          refreshBtn.disabled = false;
        }, 2000);
      });
  });

  // Unified file attachment button event listener
  attachFileBtn.addEventListener("click", function () {
    console.log("CLIENT: Attach File button pressed");
    // Create a hidden file input element
    const fileInput = document.createElement("input");
    fileInput.type = "file";
    fileInput.accept = ".pdf,.jpg,.jpeg,.png,.gif,.bmp,.tiff,.webp"; // Accept both PDF and image formats
    fileInput.style.display = "none";

    // Append to the body to make it interactable (though hidden)
    document.body.appendChild(fileInput);

    // Listen for file selection
    fileInput.addEventListener("change", function (event) {
      const file = event.target.files[0];
      if (file) {
        console.log("CLIENT: Selected file:", file.name, file.type, file.size);

        // Determine file type
        const isPDF =
          file.type === "application/pdf" ||
          file.name.toLowerCase().endsWith(".pdf");
        const isImage =
          file.type.startsWith("image/") ||
          /\.(jpg|jpeg|png|gif|bmp|tiff|webp)$/i.test(file.name);

        if (!isPDF && !isImage) {
          displayError("Please select a valid PDF or image file.");
          document.body.removeChild(fileInput);
          return;
        }

        // Validate file size
        const maxSize = 20 * 1024 * 1024; // 20MB
        if (file.size > maxSize) {
          displayError(
            `File size (${(file.size / 1024 / 1024).toFixed(
              2
            )}MB) exceeds maximum allowed size (20MB)`
          );
          document.body.removeChild(fileInput);
          return;
        }

        // Store the file based on type
        if (isPDF) {
          selectedPdfFile = file;
        } else if (isImage) {
          selectedImageFile = file;
        }

        // Update file display
        updateFileDisplay();

        // Enable send button
        sendBtn.disabled = false;
        userInput.placeholder = "💬 Add comment or send now...";
      } else {
        userInput.placeholder =
          "Enter CAD description or paste image (Ctrl+V), or drag and drop file here...";
      }
      // Clean up the input element after use
      document.body.removeChild(fileInput);
    });

    // Programmatically click the hidden file input
    fileInput.click();
  });

  // Function to update file display
  function updateFileDisplay() {
    let displayHTML = "";

    if (selectedPdfFile) {
      const fileSize = selectedPdfFile.size > 1024 * 1024
        ? `${(selectedPdfFile.size / 1024 / 1024).toFixed(1)} MB`
        : `${(selectedPdfFile.size / 1024).toFixed(1)} KB`;

      displayHTML += `
        <div class="file-attachment-card gradient-border">
          <div class="file-preview-container">
            <div class="file-icon-wrapper pdf-gradient">
              <i class="fas fa-file-pdf"></i>
            </div>
          </div>
          <div class="file-details">
            <div class="file-name">${selectedPdfFile.name}</div>
            <div class="file-meta">
              <span class="file-size">${fileSize}</span>
              <span class="file-separator">•</span>
              <span class="file-type-badge pdf-badge">
                <i class="fas fa-file-pdf"></i> PDF
              </span>
            </div>
          </div>
          <button type="button" class="file-remove-btn" data-file-type="pdf" title="Remove file">
            <i class="fas fa-times"></i>
          </button>
        </div>`;
    }

    if (selectedImageFile) {
      const imageUrl = URL.createObjectURL(selectedImageFile);
      const fileSize = selectedImageFile.size > 1024 * 1024
        ? `${(selectedImageFile.size / 1024 / 1024).toFixed(1)} MB`
        : `${(selectedImageFile.size / 1024).toFixed(1)} KB`;

      displayHTML += `
        <div class="file-attachment-card gradient-border">
          <div class="file-preview-container">
            <img src="${imageUrl}" alt="Preview" class="image-preview" />
          </div>
          <div class="file-details">
            <div class="file-name">${selectedImageFile.name}</div>
            <div class="file-meta">
              <span class="file-size">${fileSize}</span>
              <span class="file-separator">•</span>
              <span class="file-type-badge image-badge">
                <i class="fas fa-image"></i> Image
              </span>
            </div>
          </div>
          <button type="button" class="file-remove-btn" data-file-type="image" title="Remove file">
            <i class="fas fa-times"></i>
          </button>
        </div>`;
    }

    // Show/hide file info section
    if (displayHTML) {
      selectedFileInfo.innerHTML = displayHTML;
      selectedFileInfo.classList.remove("empty:hidden");
    } else {
      selectedFileInfo.innerHTML = "";
      selectedFileInfo.classList.add("empty:hidden");
    }

    // Add event listeners for remove buttons with enhanced animation
    const removeButtons = selectedFileInfo.querySelectorAll(".file-remove-btn");
    removeButtons.forEach((button) => {
      button.addEventListener("click", function (e) {
        e.preventDefault();
        e.stopPropagation();

        const fileType = this.getAttribute("data-file-type");
        const card = this.closest('.file-attachment-card');

        // Add removal animation
        if (card) {
          card.style.transform = 'translateX(100%)';
          card.style.opacity = '0';
          card.style.transition = 'all 0.3s ease';

          setTimeout(() => {
            // Clean up object URL to prevent memory leaks
            if (fileType === "image" && selectedImageFile) {
              const imageElements = selectedFileInfo.querySelectorAll("img");
              imageElements.forEach((img) => {
                if (img.src.startsWith("blob:")) {
                  URL.revokeObjectURL(img.src);
                }
              });
              selectedImageFile = null;
            } else if (fileType === "pdf") {
              selectedPdfFile = null;
            }

            updateFileDisplay();

            // Update UI state
            if (!selectedPdfFile && !selectedImageFile) {
              userInput.placeholder =
                "Enter CAD description or paste image (Ctrl+V), or drag and drop file here...";
              sendBtn.disabled = userInput.value.trim() === "";
            }

            // Show removal notification
            showNotification(`${fileType === 'pdf' ? 'PDF' : 'Image'} file removed`, "info", 2000);
          }, 300);
        }
      });
    });
  }

  // Enhanced input handling with character count and UI updates
  userInput.addEventListener("input", function () {
    // Update character count
    const length = this.value.length;
    if (charCount) {
      charCount.textContent = length;
    }

    // Update send button state
    sendBtn.disabled =
      this.value.trim() === "" && !selectedPdfFile && !selectedImageFile;

    // Auto-resize textarea
    this.style.height = "auto";
    this.style.height = Math.min(this.scrollHeight, 200) + "px";

    // Add subtle animation for character count
    if (charCount) {
      charCount.style.transform = "scale(1.1)";
      setTimeout(() => {
        charCount.style.transform = "scale(1)";
      }, 150);
    }
  });

  // Enhanced notification system
  function showNotification(message, type = "info", duration = 4000) {
    const notification = document.createElement("div");
    const notificationId = `notification-${Date.now()}`;
    notification.id = notificationId;

    const typeStyles = {
      success: "bg-green-50 border-green-200 text-green-800",
      error: "bg-red-50 border-red-200 text-red-800",
      warning: "bg-yellow-50 border-yellow-200 text-yellow-800",
      info: "bg-blue-50 border-blue-200 text-blue-800",
    };

    const icons = {
      success: "fa-check-circle",
      error: "fa-exclamation-circle",
      warning: "fa-exclamation-triangle",
      info: "fa-info-circle",
    };

    notification.className = `fixed top-4 right-4 max-w-sm p-4 border rounded-lg shadow-lg z-50 ${typeStyles[type]} notification-enter`;
    notification.innerHTML = `
      <div class="flex items-start">
        <div class="flex-shrink-0">
          <i class="fas ${icons[type]} text-lg"></i>
        </div>
        <div class="ml-3 flex-1">
          <p class="text-sm font-medium">${message}</p>
        </div>
        <div class="ml-4 flex-shrink-0">
          <button class="notification-close inline-flex text-gray-400 hover:text-gray-600 focus:outline-none" onclick="closeNotification('${notificationId}')">
            <i class="fas fa-times text-sm"></i>
          </button>
        </div>
      </div>
      <div class="notification-progress"></div>
    `;

    document.body.appendChild(notification);

    // Auto-remove after duration
    setTimeout(() => {
      closeNotification(notificationId);
    }, duration);

    return notificationId;
  }

  // Global function to close notifications
  function closeNotification(notificationId) {
    const notification = document.getElementById(notificationId);
    if (notification) {
      notification.classList.add("notification-exit");
      setTimeout(() => {
        notification.remove();
      }, 300);
    }
  }

  // Make it available globally
  window.closeNotification = closeNotification;

  // Enhanced error display function
  function displayError(error) {
    const errorDiv = document.createElement("div");
    errorDiv.className =
      "bg-red-100 border-l-4 border-red-500 text-red-700 p-4 mb-4 rounded loading-fade-in error-shake";

    // Create a heading for the error
    const errorHeading = document.createElement("div");
    errorHeading.className = "font-bold flex items-center";
    errorHeading.innerHTML =
      '<i class="fas fa-exclamation-triangle mr-2"></i>Error';
    errorDiv.appendChild(errorHeading);

    // Add the error message
    const errorMessage = document.createElement("div");
    errorMessage.className = "mt-2";
    errorMessage.textContent = error;
    errorDiv.appendChild(errorMessage);

    // Add to code output
    codeOutput.appendChild(errorDiv);
    errorDiv.scrollIntoView({ behavior: "smooth" });

    // Show notification
    showNotification(error, "error", 6000);

    // Log the error to console for debugging
    console.error("Error displayed to user:", error);
  }

  // Show/hide paste hint on focus
  userInput.addEventListener("focus", function () {
    console.log("📝 Input focused - paste capability active");

    // Show paste hint if no content
  });

  userInput.addEventListener("blur", function () {
    // Hide paste hint
  });

  // Add paste event listener for image pasting - attach to both textarea and document
  function handleImagePaste(e) {
    console.log("🔄 PASTE EVENT DETECTED!");
    console.log("- Target:", e.target.tagName, e.target.id);
    console.log("- Event type:", e.type);
    console.log("- Has clipboardData:", !!e.clipboardData);

    // Check if clipboard contains data
    if (!e.clipboardData) {
      console.log("❌ No clipboardData available");
      return;
    }

    const items = e.clipboardData.items;
    const files = e.clipboardData.files;

    console.log("📋 Clipboard contents:");
    console.log("- Items count:", items ? items.length : 0);
    console.log("- Files count:", files ? files.length : 0);

    // Debug all clipboard items
    if (items) {
      for (let i = 0; i < items.length; i++) {
        const item = items[i];
        console.log(`  Item ${i}:`, {
          kind: item.kind,
          type: item.type,
          hasFile: !!item.getAsFile,
        });
      }
    }

    // Check for files in clipboardData.files first
    if (files && files.length > 0) {
      console.log("📁 Found files in clipboard.files");
      for (let i = 0; i < files.length; i++) {
        const file = files[i];
        console.log(`File ${i}:`, file.name, file.type, file.size);

        if (file.type.startsWith("image/")) {
          console.log("✅ Processing image file from clipboard.files");
          processClipboardImage(file, e);
          return;
        }
      }
    }

    // Check for image items
    if (items) {
      for (let i = 0; i < items.length; i++) {
        const item = items[i];

        // Check if the item is an image
        if (item.kind === "file" && item.type.indexOf("image") !== -1) {
          console.log("✅ Image found in clipboard items:", item.type);

          // Prevent default paste behavior
          e.preventDefault();

          // Get the image file from clipboard
          const file = item.getAsFile();

          if (file) {
            console.log(
              "📸 Successfully got image file:",
              file.name,
              file.size
            );
            processClipboardImage(file, e);
            return;
          } else {
            console.log("❌ Failed to get file from clipboard item");
          }
        }
      }
    }

    // If no image found, check for text that might be a data URL
    if (items) {
      for (let i = 0; i < items.length; i++) {
        const item = items[i];
        if (item.kind === "string" && item.type === "text/plain") {
          item.getAsString((text) => {
            if (text.startsWith("data:image/")) {
              console.log(
                "📝 Found data URL in text:",
                text.substring(0, 50) + "..."
              );
              // Convert data URL to file
              convertDataURLToFile(text, e);
            }
          });
        }
      }
    }

    console.log("❌ No image found in clipboard");
  }

  // Enhanced file processing with better notifications
  function processClipboardImage(file, event) {
    console.log("🖼️ Processing clipboard image:", {
      name: file.name || "clipboard-image",
      type: file.type,
      size: file.size,
    });

    // Validate file size
    const maxSize = 20 * 1024 * 1024; // 20MB
    if (file.size > maxSize) {
      const errorMsg = `Image size (${(file.size / 1024 / 1024).toFixed(
        2
      )}MB) exceeds maximum allowed size (20MB)`;
      displayError(errorMsg);
      showNotification(errorMsg, "error");
      return;
    }

    // Create a proper filename for the pasted image
    const timestamp = new Date().toISOString().replace(/[:.]/g, "-");
    const fileExtension = file.type.split("/")[1] || "png";
    const fileName = file.name || `pasted-image-${timestamp}.${fileExtension}`;

    // Create a new File object with proper name
    const namedFile = new File([file], fileName, { type: file.type });

    // Store as selected image file
    selectedImageFile = namedFile;
    console.log("✅ Image stored successfully:", fileName);

    // Update file display
    updateFileDisplay();

    // Enable send button and update placeholder
    sendBtn.disabled = false;
    userInput.placeholder = "Add comment or send";

    // Show success notification
    showNotification(`Image pasted successfully: ${fileName}`, "success", 3000);

    console.log("🎉 Image paste process completed successfully!");
  }

  // Function to convert data URL to file
  function convertDataURLToFile(dataURL, event) {
    try {
      const arr = dataURL.split(",");
      const mime = arr[0].match(/:(.*?);/)[1];
      const bstr = atob(arr[1]);
      let n = bstr.length;
      const u8arr = new Uint8Array(n);

      while (n--) {
        u8arr[n] = bstr.charCodeAt(n);
      }

      const timestamp = new Date().toISOString().replace(/[:.]/g, "-");
      const fileExtension = mime.split("/")[1] || "png";
      const fileName = `pasted-image-${timestamp}.${fileExtension}`;

      const file = new File([u8arr], fileName, { type: mime });

      event.preventDefault();
      processClipboardImage(file, event);
    } catch (error) {
      console.error("❌ Error converting data URL to file:", error);
    }
  }

  // Attach paste event to textarea
  userInput.addEventListener("paste", handleImagePaste);

  // Also attach to the form container for better coverage
  chatForm.addEventListener("paste", handleImagePaste);

  // Add global paste listener as fallback
  document.addEventListener("paste", function (e) {
    // Only handle if the focus is within our chat area or no specific target
    const isInChatArea =
      chatForm.contains(e.target) ||
      e.target === userInput ||
      e.target === document.body ||
      e.target === document;

    if (isInChatArea) {
      console.log(
        "🌐 Global paste event triggered for target:",
        e.target.tagName
      );
      handleImagePaste(e);
    }
  });

  // Enhanced drag and drop functionality
  function handleDragOver(e) {
    e.preventDefault();
    e.stopPropagation();
    chatForm.classList.add("drag-over");
  }

  function handleDragLeave(e) {
    e.preventDefault();
    e.stopPropagation();
    // Only remove class if we're leaving the form entirely
    if (!chatForm.contains(e.relatedTarget)) {
      chatForm.classList.remove("drag-over");
    }
  }

  // Enhanced drag and drop with better feedback
  function handleDrop(e) {
    e.preventDefault();
    e.stopPropagation();
    chatForm.classList.remove("drag-over");

    const files = e.dataTransfer.files;
    if (files.length > 0) {
      const file = files[0];
      console.log("🗂️ File dropped:", file.name, file.type, file.size);

      // Check if it's an image
      const isImage =
        file.type.startsWith("image/") ||
        /\.(jpg|jpeg|png|gif|bmp|tiff|webp)$/i.test(file.name);
      const isPDF =
        file.type === "application/pdf" ||
        file.name.toLowerCase().endsWith(".pdf");

      if (!isImage && !isPDF) {
        const errorMsg = "Please select a valid image or PDF file.";
        displayError(errorMsg);
        showNotification(errorMsg, "error");
        return;
      }

      // Validate file size
      const maxSize = 20 * 1024 * 1024; // 20MB
      if (file.size > maxSize) {
        const errorMsg = `File size (${(file.size / 1024 / 1024).toFixed(
          2
        )}MB) exceeds maximum allowed size (20MB)`;
        displayError(errorMsg);
        showNotification(errorMsg, "error");
        return;
      }

      // Store the file
      if (isImage) {
        selectedImageFile = file;
      } else if (isPDF) {
        selectedPdfFile = file;
      }

      // Update file display
      updateFileDisplay();

      // Enable send button and update placeholder
      sendBtn.disabled = false;
      userInput.placeholder = "💬 Add comment or send now...";

      // Show enhanced success notification
      const fileType = isImage ? "Image" : "PDF";
      showNotification(
        `${fileType} file dropped successfully: ${file.name}`,
        "success"
      );
    }
  }

  // Add drag and drop event listeners
  chatForm.addEventListener("dragover", handleDragOver);
  chatForm.addEventListener("dragleave", handleDragLeave);
  chatForm.addEventListener("drop", handleDrop);

  // Also add to the entire form container for better coverage
  const formContainer = chatForm.parentElement;
  if (formContainer) {
    formContainer.addEventListener("dragover", handleDragOver);
    formContainer.addEventListener("dragleave", handleDragLeave);
    formContainer.addEventListener("drop", handleDrop);
  }

  // Add visual feedback for paste capability
  userInput.addEventListener("focus", function () {
    console.log("📝 Input focused - paste capability active");
  });

  // Add keyboard shortcut hint
  userInput.addEventListener("keydown", function (e) {
    if (e.ctrlKey && e.key === "v") {
      console.log("🔄 Ctrl+V detected - waiting for paste event");
    }
  });



  // Development mode - only show test buttons in development
  if (
    window.location.hostname === "localhost" ||
    window.location.hostname === "127.0.0.1"
  ) {
    console.log("🛠️ Development mode detected - showing debug tools");


  }

  // Legacy compatibility - only add listeners if elements exist
  if (chatToggle && chatbotWidget) {
    chatToggle.addEventListener("click", function () {
      chatbotWidget.classList.toggle("active");
      chatToggle.classList.toggle("hidden");
    });
  }

  // Note: closeChat and minimizeChat elements don't exist in current HTML
  // Removed references to avoid errors

  // Function to update debug panel (placeholder)
  function updateDebugPanel() {
    // Debug panel update logic can be added here if needed
    console.log("Debug panel updated");
  }

  // Edit mode toggle
  editModeToggle.addEventListener("change", function () {
    isEditMode = this.checked;
    console.log(
      `🎯 [EDIT MODE DEBUG] Edit mode toggled: ${isEditMode ? "ON" : "OFF"}`
    );
    console.log(
      `📋 [EDIT MODE DEBUG] Current session ID: ${currentSessionId || "None"}`
    );
    console.log(
      `💾 [EDIT MODE DEBUG] Latest code available: ${latestCode ? "Yes" : "No"}`
    );

    // Update debug panel
    updateDebugPanel();

    if (isEditMode) {
      editModeIndicator.classList.remove("hidden");
      userInput.placeholder = "Enter edit request...";

      // Disable edit mode if no code has been generated yet
      if (!latestCode) {
        // Check if we have a current session to get code from
        if (currentSessionId) {
          console.log(
            `🔍 [EDIT MODE DEBUG] Fetching latest code for session: ${currentSessionId}`
          );

          // Try to get the latest code for the current session
          authenticatedFetch(`/api-production/sessions/${currentSessionId}/latest-code`)
            .then((response) => {
              console.log(
                `📡 [EDIT MODE DEBUG] API response status: ${response.status}`
              );
              if (response.ok) {
                return response.json();
              } else {
                throw new Error(
                  `Failed to get latest code: ${response.status}`
                );
              }
            })
            .then((data) => {
              console.log(`📋 [EDIT MODE DEBUG] Received data:`, data);

              if (data.latest_code) {
                console.log(
                  `✅ [EDIT MODE DEBUG] Found code for session ${data.session_id}, length: ${data.latest_code.length} characters`
                );
                console.log(
                  `🔍 [EDIT MODE DEBUG] Code preview: ${data.latest_code.substring(
                    0,
                    100
                  )}...`
                );

                // Verify session ID matches
                if (data.session_id !== currentSessionId) {
                  console.error(
                    `❌ [EDIT MODE DEBUG] SESSION MISMATCH! Expected: ${currentSessionId}, Got: ${data.session_id}`
                  );
                  alert(
                    `ERROR: Session mismatch!\nExpected: ${currentSessionId}\nReceived: ${data.session_id}`
                  );
                  return;
                }

                // Use the latest code from current session
                displayCode(data.latest_code);
                latestCode = data.latest_code;

                // Update debug panel
                updateDebugPanel();

                // Show notification with session info
                const notification = document.createElement("div");
                notification.className =
                  "text-sm text-green-600 mt-1 mb-2 fade-out";
                notification.textContent = `Loaded latest code from session ${data.session_id} for editing.`;
                notification.style.animation = "fadeOut 3s forwards";

                // Add the notification before the form
                chatForm.parentNode.insertBefore(notification, chatForm);

                // Remove the notification after 3 seconds
                setTimeout(() => {
                  notification.remove();
                }, 3000);
              } else {
                console.log(
                  `❌ [EDIT MODE DEBUG] No code found for session ${currentSessionId}`
                );
                // No code in current session, disable edit mode
                alert(
                  "No code has been generated in this session yet. Generate code first before using edit mode."
                );
                editModeToggle.checked = false;
                isEditMode = false;
                editModeIndicator.classList.add("hidden");
                userInput.placeholder = "Enter CAD description...";
              }
            })
            .catch((error) => {
              console.error(
                "Error loading latest code for current session:",
                error
              );
              // Fallback: disable edit mode
              alert(
                "No code has been generated in this session yet. Generate code first before using edit mode."
              );
              editModeToggle.checked = false;
              isEditMode = false;
              editModeIndicator.classList.add("hidden");
              userInput.placeholder =
                "Enter CAD description or paste image (Ctrl+V), or drag and drop file here...";
            });
        } else {
          // No current session, disable edit mode
          alert(
            "No active session found. Start a conversation first before using edit mode."
          );
          editModeToggle.checked = false;
          isEditMode = false;
          editModeIndicator.classList.add("hidden");
          userInput.placeholder =
            "Enter CAD description or paste image (Ctrl+V), or drag and drop file here...";
        }
      } else {
        editModeIndicator.classList.add("hidden");
        userInput.placeholder =
          "Enter CAD description or paste image (Ctrl+V), or drag and drop file here...";
      }
    }
  });

  // Form submission handler
  if (chatForm) {
    console.log("Adding form submit listener to chatForm");
    chatForm.addEventListener("submit", function (e) {
      e.preventDefault();
      console.log("Form submitted - calling handleChatSubmission");
      handleChatSubmission();
    });
  } else {
    console.error("chatForm element not found!");
  }

  // Functions for real-time progress
  function showProcessingProgress() {
    console.log("🎬 Showing processing progress section");

    if (processingProgressSection) {
      // Show the progress section with animation
      processingProgressSection.classList.remove("hidden");
      processingProgressSection.style.opacity = "0";
      processingProgressSection.style.transform = "translateY(-10px)";

      requestAnimationFrame(() => {
        processingProgressSection.style.transition =
          "opacity 0.3s ease, transform 0.3s ease";
        processingProgressSection.style.opacity = "1";
        processingProgressSection.style.transform = "translateY(0)";
      });

      // Reset to initial state immediately
      updateOverallProgress(0, 0);

      // Reset all steps to waiting state
      Object.keys(progressSteps).forEach((stepKey) => {
        const step = progressSteps[stepKey];
        if (step && step.icon && step.status) {
          // Reset immediately without animation delay
          step.status.textContent = "Waiting...";
          step.icon.innerHTML = '<i class="fas fa-hourglass-start"></i>';
          step.status.className = "text-sm text-gray-500 whitespace-nowrap";
          step.icon.className =
            "flex-shrink-0 w-8 h-8 bg-gray-100 rounded-full flex items-center justify-center text-gray-500 transition-all duration-300";

          if (stepKey === "export") {
            updateExportEta({});
          }

          // Mark as not completed
          progressSteps[stepKey].completed = false;
        }
      });
    }

    // Hide results while processing
    const resultsSection = document
      .getElementById("code-output")
      .closest(".bg-white.rounded-xl");
    if (resultsSection) {
      resultsSection.classList.add("hidden");
    }
  }

  function hideProcessingProgress() {
    if (processingProgressSection) {
      processingProgressSection.classList.add("hidden");
    }
    const resultsSection = document
      .getElementById("code-output")
      .closest(".bg-white.rounded-xl");
    if (resultsSection) {
      resultsSection.classList.remove("hidden"); // Show results again
    }
  }

  function ensureExportEtaElement() {
    const exportStep = progressSteps.export;
    if (!exportStep) return null;

    if (exportStep.eta) return exportStep.eta;

    const exportStatus = exportStep.status || document.getElementById("step-status-export");
    if (!exportStatus || !exportStatus.parentElement) return null;

    const etaElement = document.createElement("div");
    etaElement.id = "step-eta-export";
    etaElement.className = "text-xs text-indigo-600 mt-1 hidden";
    etaElement.textContent = "Estimated time: --";
    exportStatus.parentElement.appendChild(etaElement);
    exportStep.eta = etaElement;
    return etaElement;
  }

  function formatExportEtaText(data) {
    const remaining = Number.parseInt(data.estimated_time_seconds, 10);
    const initial = Number.parseInt(data.initial_estimated_time_seconds, 10);
    const elapsed = Number.parseInt(data.estimated_elapsed_seconds, 10);
    const remainingLabel = data.estimated_time_label || (
      Number.isFinite(remaining) ? `${remaining}s` : ""
    );
    const initialLabel = data.initial_estimated_time_label || (
      Number.isFinite(initial) ? `${initial}s` : ""
    );
    const elapsedLabel = Number.isFinite(elapsed) ? `${elapsed}s elapsed` : "";

    if (!remainingLabel) return "";

    if (data.is_estimate_overrun) {
      return [
        `<div>Remaining: estimate exceeded</div>`,
        initialLabel ? `<div>Initial estimate: ${initialLabel}</div>` : "",
        elapsedLabel ? `<div>${elapsedLabel}. Export is still running...</div>` : `<div>Export is still running...</div>`,
      ].filter(Boolean).join("");
    }

    return [
      `<div>Remaining: ${remainingLabel}</div>`,
      initialLabel ? `<div>Initial estimate: ${initialLabel}</div>` : "",
      elapsedLabel ? `<div>${elapsedLabel}</div>` : "",
    ].filter(Boolean).join("");
  }

  function updateExportEta(data = {}) {
    const etaElement = ensureExportEtaElement();
    if (!etaElement) return;

    const hasEta = data.step === "export" && data.estimated_time_seconds !== undefined;
    if (!hasEta || data.is_complete) {
      etaElement.classList.add("hidden");
      etaElement.textContent = "Estimated time: --";
      return;
    }

    const etaText = formatExportEtaText(data);
    if (!etaText) {
      etaElement.classList.add("hidden");
      return;
    }

    etaElement.innerHTML = etaText;
    etaElement.className = data.is_estimate_overrun
      ? "text-xs text-amber-600 mt-1"
      : "text-xs text-indigo-600 mt-1";
  }

  function updateStepProgress(
    stepId,
    statusText,
    iconClass,
    isComplete,
    isActive = false
  ) {
    const step = progressSteps[stepId];
    if (step) {
      // Force immediate update with requestAnimationFrame
      requestAnimationFrame(() => {
        step.status.textContent = statusText;
        step.icon.innerHTML = `<i class="${iconClass}"></i>`;

        if (isComplete) {
          step.status.className = "text-sm text-green-600 whitespace-nowrap";
          step.icon.className =
            "flex-shrink-0 w-8 h-8 bg-green-100 rounded-full flex items-center justify-center text-green-600 transition-all duration-300";

          // Add completed animation
          step.icon.style.transform = "scale(1.1)";
          setTimeout(() => {
            step.icon.style.transform = "scale(1)";
          }, 200);
        } else if (isActive) {
          step.status.className = "text-sm text-indigo-600 whitespace-nowrap";
          step.icon.className =
            "flex-shrink-0 w-8 h-8 bg-indigo-100 rounded-full flex items-center justify-center text-indigo-600 animate-pulse transition-all duration-300";
        } else {
          step.status.className = "text-sm text-gray-500 whitespace-nowrap";
          step.icon.className =
            "flex-shrink-0 w-8 h-8 bg-gray-100 rounded-full flex items-center justify-center text-gray-500 transition-all duration-300";
        }

        // Force style recalculation
        step.status.offsetHeight;
        step.icon.offsetHeight;
      });
    }
  }

  function updateOverallProgress(percentage, stepsCompleted) {
    if (
      overallProgressPercentage &&
      overallProgressBar &&
      overallProgressText
    ) {
      // Force immediate update with requestAnimationFrame
      requestAnimationFrame(() => {
        // Update percentage with animation
        const currentPercentage =
          parseInt(overallProgressPercentage.textContent) || 0;
        if (percentage !== currentPercentage) {
          overallProgressPercentage.textContent = `${percentage}%`;
          overallProgressPercentage.style.transform = "scale(1.1)";
          setTimeout(() => {
            overallProgressPercentage.style.transform = "scale(1)";
          }, 200);
        }

        // Animate progress bar
        overallProgressBar.style.transition = "width 0.5s ease-in-out";
        overallProgressBar.style.width = `${percentage}%`;

        // Update text
        overallProgressText.textContent = `${stepsCompleted} of ${totalSteps} steps completed`;

        // Force style recalculation
        overallProgressPercentage.offsetHeight;
        overallProgressBar.offsetWidth;
        overallProgressText.offsetHeight;
      });
    }
  }

  function startRealtimeProgress(message, isEditRequest, sessionId) {
    if (eventSource) {
      eventSource.close();
    }

    // ✅ Lock UI ngay khi bắt đầu stream
    setUILocked(true);

    // Pre-flight authentication check
    if (!window.apiToken) {
      addChatMessage("Error: Authentication token is missing. Please check your API token and try again.", "bot");
      setUILocked(false);
      return;
    }

    const queryParams = new URLSearchParams({
      message: message,
      is_edit_request: isEditRequest,
    });
    const selectedPriority = getSelectedPriority();
    queryParams.append("priority", selectedPriority.toString());

    if (sessionId) {
      queryParams.append("session_id", sessionId);
    }

    // Add material choice from the selector
    const materialChoice = window.getSelectedMaterial ? window.getSelectedMaterial() : 'STEEL';
    queryParams.append("material_choice", materialChoice);
    console.log('[MATERIAL] SSE request with material:', materialChoice);
    console.log('[PRIORITY] SSE request with priority:', selectedPriority);

    // Add token for authentication (EventSource doesn't support headers)
    if (window.apiToken) {
      queryParams.append("token", window.apiToken);
    }

    console.log(
      "🚀 Starting SSE connection to:",
      `/api/generate-cad-stream?${queryParams.toString()}`
    );

    eventSource = new EventSource(
      `/api/generate-cad-stream?${queryParams.toString()}`
    );
    let completedStepsCount = 0;

    // Initialize all steps as not completed
    Object.keys(progressSteps).forEach((stepKey) => {
      progressSteps[stepKey].completed = false;
    });

    eventSource.onopen = function (event) {
      console.log("✅ SSE connection opened successfully");
    };

    eventSource.onmessage = function (event) {
      try {
        const data = JSON.parse(event.data);
        console.log("📦 SSE data received:", data);

        if (data.step) {
          const {
            step,
            status,
            icon,
            is_complete,
            is_active,
            overall_percentage,
            message: stepMessage,
          } = data;

          console.log(
            `🔄 Updating step: ${step} - ${status} (${overall_percentage}%)`
          );

          // Update step progress immediately (only for known steps)
          if (progressSteps[step]) {
            updateStepProgress(
              step,
              stepMessage || status,
              icon || "fas fa-cogs",
              is_complete,
              is_active
            );
            if (step === "export") {
              updateExportEta(data);
            }
          }

          // Count completed steps (only for known steps in progressSteps)
          if (is_complete && progressSteps[step] && !progressSteps[step].completed) {
            progressSteps[step].completed = true;
            completedStepsCount++;
            console.log(
              `✅ Step ${step} completed. Total completed: ${completedStepsCount}`
            );
          }

          // Update overall progress immediately
          updateOverallProgress(overall_percentage || 0, completedStepsCount);

          // Force DOM refresh
          requestAnimationFrame(() => {
            // This ensures the UI updates are processed immediately
          });
        }

        // 🆕 Handle session_id event - Update session indicator immediately
        if (data.session_id && data.step === "session_initialized") {
          console.log("🆔 Session initialized:", data.session_id);
          updateSessionIndicator(data.session_id);
          // Also display in console for debugging
          console.log(`%c📋 Session ID: ${data.session_id}`, 'color: #4299e1; font-weight: bold; font-size: 14px;');
        }

        if (data.final_response) {
          console.log("🎉 Final response received:", data.final_response);
          hideProcessingProgress();
          hideAIThinkingMessage(); // Hide AI thinking message
          eventSource.close();
          // ✅ Unlock UI sau khi stream hoàn thành thành công
          setUILocked(false);

          const finalData = data.final_response;

          // Update session indicator
          if (finalData.session_id) {
            updateSessionIndicator(finalData.session_id);
          }

          // ═══════════════════════════════════════════════════════════
          // UNIFIED RESPONSE RENDERING (Non-streaming endpoint)
          // Web content is now included in the message itself
          // No need to separately render web_search_metadata
          // ═══════════════════════════════════════════════════════════
          console.log("📋 URL info request (non-streaming):", finalData.is_url_info_request);

          // Check if this is a URL-based information request
          if (finalData.is_url_info_request) {
            console.log("🌐 URL-based information request - using unified message format (non-streaming)");
          }

          // Display the response
          if (finalData.chat_response) {
            addChatMessage(finalData.chat_response, "bot");
            if (isConfirmPromptText(finalData.chat_response)) {
              markAwaitingConfirmReply(true, "confirm prompt received");
            }

            // Load OBJ button is always visible, no need to show/hide based on exports
          } else {
            addChatMessage(
              "Received an unexpected final response format from the server.",
              "bot"
            );
          }

          // Update download panel with file information
          if (window.downloadManager) {
            window.downloadManager.updateFromResponse(finalData);
          }

          // Load PDF viewer if technical drawing is available
          if (window.pdfViewer && (finalData.technical_drawing_export || finalData.technical_drawing_path)) {
            const pdfPath = finalData.technical_drawing_export || finalData.technical_drawing_path;
            const pdfFilename = pdfPath.split('/').pop() || 'technical-drawing.pdf';
            console.log('📄 Loading PDF viewer:', pdfPath);
            window.pdfViewer.loadPDF(pdfPath, pdfFilename);
          }

          // ═══════════════════════════════════════════════════════════
          // OPTIMIZED 3D MODEL LOADING - Single unified call
          // Use handleCADGenerationResponse for centralized model loading
          // This avoids duplicate API requests for OBJ and JSON files
          // ═══════════════════════════════════════════════════════════
          if (typeof window.handleCADGenerationResponse === 'function') {
            console.log("🔄 Loading 3D models via unified handler (prevents duplicates)");
            window.handleCADGenerationResponse(finalData);
          } else {
            // Fallback: Manual loading only if unified handler not available
            console.warn("⚠️ Unified handler not available, using fallback loading");

            // Load OBJ model
            if (finalData.obj_export && (modelViewer || window.dualViewer?.objViewer)) {
              const objViewer = window.dualViewer?.objViewer || modelViewer;
              console.log("🎯 [Fallback] Loading OBJ model:", finalData.obj_export);

              let objUrl = finalData.obj_export.startsWith('/api/3d-viewer/')
                ? finalData.obj_export
                : `/api/3d-viewer/${finalData.obj_export}`;

              objViewer.loadModel(objUrl, finalData.obj_export.split("/").pop() || "model.obj")
                .catch(error => console.error("❌ Error loading OBJ:", error));
            }

            // Load STEP model
            const stepPath = finalData.step_export || finalData.step_path;
            if (stepPath && (window.stepViewer || window.dualViewer?.stepViewer)) {
              const stepViewer = window.stepViewer || window.dualViewer?.stepViewer;
              console.log("🎯 [Fallback] Loading STEP model:", stepPath);

              let stepUrl = stepPath.startsWith('/api/step-viewer/')
                ? stepPath
                : `/api/step-viewer/${stepPath}`;

              stepViewer.loadModel(stepUrl, stepPath.split("/").pop() || "model.step")
                .catch(error => console.error("❌ Error loading STEP:", error));
            }
          }

          // Display generated code if available
          if (finalData.code) {
            displayCode(finalData.code);
            latestCode = finalData.code;
          }

          // Ensure results section is visible
          const resultsSection = document
            .getElementById("code-output")
            .closest(".bg-white.rounded-xl");
          if (resultsSection) {
            resultsSection.classList.remove("hidden");
          }
        }

        if (data.error) {
          console.error("❌ SSE Error received:", data.error);
          hideProcessingProgress();
          hideAIThinkingMessage(); // Hide AI thinking message
          addChatMessage("Error: " + data.error, "bot");
          eventSource.close();
          // ✅ Unlock UI khi stream lỗi
          setUILocked(false);

          // Ensure results section is visible
          const resultsSection = document
            .getElementById("code-output")
            .closest(".bg-white.rounded-xl");
          if (resultsSection) {
            resultsSection.classList.remove("hidden");
          }
        }
      } catch (parseError) {
        console.error(
          "❌ Error parsing SSE data:",
          parseError,
          "Raw data:",
          event.data
        );
      }
    };

    eventSource.onerror = function (error) {
      console.error("❌ EventSource failed:", error);
      console.error("❌ EventSource readyState:", eventSource.readyState);

      // Only handle real errors, not just connection attempts
      if (eventSource.readyState === EventSource.CLOSED) {
        hideProcessingProgress();
        hideAIThinkingMessage(); // Hide AI thinking message

        // Provide more specific error messages based on readyState
        let errorMessage = "Connection to the server was lost. This could be due to authentication issues or network problems.";

        addChatMessage("Error: " + errorMessage + " Please try again.", "bot");
        eventSource.close();
        // ✅ Unlock UI khi EventSource mất kết nối
        setUILocked(false);

        // Ensure results section is visible
        const resultsSection = document
          .getElementById("code-output")
          .closest(".bg-white.rounded-xl");
        if (resultsSection) {
          resultsSection.classList.remove("hidden");
        }
      } else if (eventSource.readyState === EventSource.CONNECTING) {
        // Connection is still trying, don't show error yet
        console.log("⏳ SSE still connecting...");
      }
    };
  }

  // DEPRECATED: Enhanced typing indicator - not used anymore
  function showEnhancedTypingIndicator() {
    console.log('⚠️ DEPRECATED: showEnhancedTypingIndicator called - use showTypingIndicator() instead');
    return; // Disabled - use standard typing indicator
  }

  // Remove enhanced typing indicator
  function removeEnhancedTypingIndicator() {
    // Remove from main chat
    hideTypingIndicator();

    // Remove from code output
    const typingIndicator = document.getElementById("typing-indicator-code");
    if (typingIndicator) {
      typingIndicator.classList.add("loading-fade-out");
      setTimeout(() => {
        typingIndicator.remove();
      }, 300);
    }
  }

  // New function to display generated code
  function displayCode(code) {
    // Clear empty state if it exists
    clearEmptyState();

    const codeBlock = document.createElement("pre");
    const codeElement = document.createElement("code");

    // Check if this is a parameter request and style it accordingly
    if (
      code.includes("I need some more information") ||
      code.includes("Please provide the following parameters") ||
      code.includes("Sheet length") ||
      code.includes("Hole type")
    ) {
      console.log(
        "Detected parameter request in displayCode, applying special styling"
      );
      codeBlock.style.backgroundColor = "#e8f4ff";
      codeBlock.style.borderLeft = "3px solid #4299e1";
      codeBlock.style.color = "#2c5282";
      codeBlock.style.padding = "8px 12px";
      codeBlock.style.borderRadius = "8px";
      codeBlock.style.maxWidth = "100%";
      codeBlock.style.marginBottom = "10px";
      codeBlock.classList.add("parameter-request");
    }

    codeElement.textContent = code;
    codeBlock.appendChild(codeElement);
    codeOutput.appendChild(codeBlock);
    codeBlock.scrollIntoView({ behavior: "smooth" });

    // Load OBJ button is always visible, no need to show/hide based on code content
  }

  // Function to clear empty state
  function clearEmptyState() {
    const emptyState = codeOutput.querySelector(
      ".flex.flex-col.items-center.justify-center"
    );
    if (emptyState) {
      emptyState.remove();
    }
  }

  // Chat history functions

  // Function to load chat history
  function loadChatHistory() {
    // Clear previous history
    chatHistory.innerHTML = "";

    // Show loading indicator
    const loadingIndicator = document.createElement("div");
    loadingIndicator.className = "text-center text-gray-500 py-2";
    loadingIndicator.textContent = "Loading chat history...";
    chatHistory.appendChild(loadingIndicator);

    // Fetch chat history from API
    const headers = {};
    if (window.apiToken) {
      headers['Authorization'] = `Bearer ${window.apiToken}`;
    }
    fetch("/api/chat-history", { headers: headers })
      .then((response) => response.json())
      .then((data) => {
        // Remove loading indicator
        chatHistory.innerHTML = "";

        // Check if history exists
        if (data.history && data.history.length > 0) {
          // Display history entries
          data.history.forEach((entry) => {
            displayHistoryEntry(entry);
          });
        } else {
          // Display empty message
          const emptyMessage = document.createElement("p");
          emptyMessage.className = "text-gray-400";
          emptyMessage.textContent = "No chat history found.";
          chatHistory.appendChild(emptyMessage);
        }
      })
      .catch((error) => {
        console.error("Error loading chat history:", error);
        chatHistory.innerHTML = "";
        const errorMessage = document.createElement("p");
        errorMessage.className = "text-red-500";
        errorMessage.textContent = "Failed to load chat history.";
        chatHistory.appendChild(errorMessage);
      });
  }

  // Function to display a history entry
  function displayHistoryEntry(entry) {
    const entryDiv = document.createElement("div");
    entryDiv.className = "mb-4 p-3 bg-white rounded shadow-sm";

    // Format timestamp
    const timestamp = new Date(entry.timestamp);
    const formattedDate = timestamp.toLocaleDateString();
    const formattedTime = timestamp.toLocaleTimeString();

    // Create entry content
    entryDiv.innerHTML = `
      <div class="flex justify-between items-start mb-2">
        <div class="font-medium ${entry.is_edit_request ? "text-indigo-600" : "text-gray-700"
      }">
          ${entry.is_edit_request
        ? '<i class="fas fa-pencil-alt mr-1"></i> Edit Request'
        : '<i class="fas fa-comment mr-1"></i> New Shape'
      }
        </div>
        <div class="text-xs text-gray-500">${formattedDate} ${formattedTime}</div>
      </div>
      <div class="text-sm text-gray-600 mb-2">${entry.user_message}</div>
      <div class="text-xs text-gray-500 flex justify-end">
        <button class="use-code-btn text-indigo-600 hover:text-indigo-800 transition">
          <i class="fas fa-code mr-1"></i> Use This Code
        </button>
      </div>
    `;

    // Add event listener to the "Use This Code" button
    const useCodeBtn = entryDiv.querySelector(".use-code-btn");
    useCodeBtn.addEventListener("click", function () {
      // Display the code in the code output area
      displayCode(entry.generated_code);
      // Store the code as the latest code
      latestCode = entry.generated_code;

      // Send a request to update the server-side latest code
      authenticatedFetch("/api/update-latest-code", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          code: entry.generated_code,
          session_id: currentSessionId, // Include session_id to update database
        }),
      })
        .then((response) => response.json())
        .then((data) => {
          if (data.success) {
            console.log("Latest code updated successfully:", data.message);
          } else {
            console.warn("Failed to update latest code:", data.message);
          }
        })
        .catch((error) => console.error("Error updating latest code:", error));

      // Enable edit mode automatically
      if (!isEditMode) {
        editModeToggle.checked = true;
        isEditMode = true;
        editModeIndicator.classList.remove("hidden");
        userInput.placeholder = "Enter edit request...";
      }

      // Show notification
      const notification = document.createElement("div");
      notification.className = "text-sm text-green-600 mt-1 mb-2 fade-out";
      notification.textContent =
        "Code loaded from history. Edit mode enabled for modifications.";
      notification.style.animation = "fadeOut 3s forwards";

      // Add the notification before the code output
      codeOutput.parentNode.insertBefore(notification, codeOutput);

      // Remove the notification after 3 seconds
      setTimeout(() => {
        notification.remove();
      }, 3000);

      // Focus on the input field for immediate editing
      userInput.focus();
    });

    // Add the entry to the chat history
    chatHistory.appendChild(entryDiv);
  }

  // Function to clear chat history
  function clearChatHistory() {
    // Show confirmation dialog
    if (
      confirm(
        "Are you sure you want to clear all chat history? This action cannot be undone."
      )
    ) {
      // Send request to API
      const headers = {};
      if (window.apiToken) {
        headers['Authorization'] = `Bearer ${window.apiToken}`;
      }
      fetch("/api/chat-history", {
        method: "DELETE",
        headers: headers,
      })
        .then((response) => response.json())
        .then((data) => {
          // Reload chat history
          loadChatHistory();

          // Show notification
          showNotification(
            "Chat history cleared successfully.",
            "success",
            3000
          );
        })
        .catch((error) => {
          console.error("Error clearing chat history:", error);
          alert("Failed to clear chat history. Please try again.");
        });
    }
  }

  // Add event listener to clear history button (if it exists)
  const clearHistoryBtn = document.getElementById("clear-history-btn");
  if (clearHistoryBtn) {
    clearHistoryBtn.addEventListener("click", clearChatHistory);
  }

  // Load chat history when the page loads
  loadChatHistory();

  // The fetch interceptor for reloading chat history has been removed as it is not compatible with EventSource.
  // History is now reloaded upon successful completion of the stream in the onmessage handler.

  // Removed saved shapes logic
  // Function to display messages
  function displayMessage(message, sender) {
    // Clear empty state if it exists
    clearEmptyState();

    // Add message to chat interface
    addChatMessage(message, sender);

    // Also add to code output for compatibility
    const messageElement = document.createElement("div");
    messageElement.className = `message ${sender}`;

    // Check if this is a message asking for parameters
    if (sender === "bot") {
      console.log("Creating bot message with class:", messageElement.className);
      // Apply bot styles for code output compatibility
      messageElement.style.backgroundColor = "#ffffff";
      messageElement.style.border = "1px solid #e5e7eb";
      messageElement.style.color = "#374151";
      messageElement.style.padding = "12px 16px";
      messageElement.style.borderRadius = "18px 18px 18px 4px";
      messageElement.style.maxWidth = "90%";
      messageElement.style.marginBottom = "10px";
      messageElement.style.boxShadow = "0 2px 8px rgba(0, 0, 0, 0.1)";
      // Extract questions from the message if they're in a list format
      const questions = [];
      const lines = message.split("\n");

      // Look for patterns that indicate this is a parameter request
      const isParameterRequest =
        (message.includes("missing") &&
          (message.includes("parameter") || message.includes("information"))) ||
        (message.includes("provide") && message.includes("following")) ||
        (message.includes("need") &&
          (message.includes("information") || message.includes("parameter"))) ||
        (message.includes("sheet") && message.includes("thickness")) ||
        (message.includes("hole") &&
          (message.includes("radius") || message.includes("pattern")));

      // Count how many numbered list items we have
      let numberedItems = 0;

      for (const line of lines) {
        // Look for numbered list items (e.g., "1. What is the length?")
        const match = line.match(/^\s*\d+\.\s+(.+)$/);
        if (match) {
          questions.push(match[1].trim());
          numberedItems++;
        }
      }

      // Special case for the exact format shown in the screenshot
      if (
        message.includes("Please provide the following missing information") &&
        (message.includes("Sheet thickness") ||
          message.includes("thickness")) &&
        (message.includes("Hole radius") || message.includes("radius")) &&
        (message.includes("Hole pattern") || message.includes("pattern"))
      ) {
        console.log("Detected specific perforated sheet parameter request");

        // Extract explanation if present
        let explanation = "";
        if (
          message.includes("Need") &&
          message.includes("to model the sheet")
        ) {
          const lines = message.split("\n");
          for (const line of lines) {
            if (line.includes("Need") && line.includes("to model the sheet")) {
              explanation = line.trim();
              break;
            }
          }
        }

        // Display the parameter table
        displayParameterTable([
          "Sheet thickness",
          "Hole radius",
          "Hole pattern",
        ]);

        // If we found an explanation, display it separately
        if (explanation) {
          setTimeout(() => {
            displayMessage(`<i>${explanation}</i>`, "bot-explanation");
          }, 500);
        }

        return;
      }

      // If we have a parameter request message with numbered items, display as a table
      if ((isParameterRequest || numberedItems >= 2) && questions.length > 0) {
        console.log(
          "Detected parameter request, displaying table with questions:",
          questions
        );
        displayParameterTable(questions);
        return;
      }
    }

    // Format the message with line breaks (for non-parameter messages)
    messageElement.innerHTML = message.replace(/\n/g, "<br>");

    // Add to code output container for compatibility
    if (codeOutput) {
      codeOutput.appendChild(messageElement);
      // Scroll to the new message
      messageElement.scrollIntoView({ behavior: "smooth" });
    }
  }

  // Function to display a table of missing parameters
  function displayParameterTable(questions) {
    console.log("Creating parameter table with questions:", questions);

    // Instead of creating a parameter table, display as normal chat messages
    const messageElement = document.createElement("div");
    messageElement.className = "message bot parameter-message";

    // Apply bot styles directly to ensure they take effect
    messageElement.style.backgroundColor = "#e8f4ff";
    messageElement.style.borderLeft = "3px solid #4299e1";
    messageElement.style.color = "#2c5282";
    messageElement.style.padding = "8px 12px";
    messageElement.style.borderRadius = "8px";
    messageElement.style.maxWidth = "90%";
    messageElement.style.marginBottom = "10px";

    // Create a list of questions
    let messageContent =
      "I need some more information. Please provide the following parameters:<br><ul>";
    questions.forEach((question) => {
      messageContent += `<li>${question}</li>`;
    });
    messageContent += "</ul>";

    messageElement.innerHTML = messageContent;
    codeOutput.appendChild(messageElement);

    // Log for debugging
    console.log("Added parameter message with styles:", {
      backgroundColor: messageElement.style.backgroundColor,
      borderLeft: messageElement.style.borderLeft,
      element: messageElement,
    });

    messageElement.scrollIntoView({ behavior: "smooth" });

    /* Original table code - commented out but preserved
    // Create container for the parameter form
    const formContainer = document.createElement("div");
    formContainer.className =
      "parameter-form bg-white border border-indigo-200 rounded-lg p-4 mb-4";
    formContainer.id = "parameter-form-container";

    // Create form element
    const form = document.createElement("form");
    form.className = "space-y-4";

    // Create table for parameters
    const table = document.createElement("table");
    table.className = "w-full border-collapse";

    // Add table header (with Vietnamese translation)
    const thead = document.createElement("thead");
    const headerRow = document.createElement("tr");
    const isVietnamese = document.documentElement.lang === "vi";
    headerRow.innerHTML = `
      <th class="text-left py-2 px-3 bg-indigo-100 border-b-2 border-indigo-200">Parameter</th>
      <th class="text-left py-2 px-3 bg-indigo-100 border-b-2 border-indigo-200">Value</th>
    `;
    thead.appendChild(headerRow);
    table.appendChild(thead);

    // Add table body
    const tbody = document.createElement("tbody");

    // Add a row for each missing parameter
    const cleanedQuestions = questions.map((q) => {
      // Remove trailing punctuation and question marks
      let cleaned = q.replace(/[?:.,;!]+$/, "").trim();
      // If the question is just "Sheet thickness", "Hole radius", etc., add a label
      if (
        cleaned.toLowerCase().includes("thickness") &&
        !cleaned.includes("=")
      ) {
        cleaned = "Sheet thickness";
      }
      if (cleaned.toLowerCase().includes("radius") && !cleaned.includes("=")) {
        cleaned = "Hole radius";
      }
      if (cleaned.toLowerCase().includes("pattern") && !cleaned.includes("=")) {
        cleaned = "Hole pattern";
      }
      return cleaned;
    });

    cleanedQuestions.forEach((question, index) => {
      const row = document.createElement("tr");
      row.className = index % 2 === 0 ? "bg-gray-50" : "bg-white";

      // Parameter name cell
      const nameCell = document.createElement("td");
      nameCell.className = "py-2 px-3 border-b border-gray-200";
      nameCell.textContent = question;

      // Parameter value cell
      const valueCell = document.createElement("td");
      valueCell.className = "py-2 px-3 border-b border-gray-200";

      // Create input for parameter value
      const input = document.createElement("input");
      input.type = "text";
      input.name = `param_${index}`;
      input.className =
        "w-full border border-gray-300 rounded px-3 py-1 text-sm focus:outline-none focus:ring-1 focus:ring-indigo-500";
      input.placeholder = "Enter value";
      input.required = true;

      valueCell.appendChild(input);

      // Add cells to row
      row.appendChild(nameCell);
      row.appendChild(valueCell);

      // Add row to table body
      tbody.appendChild(row);
    });

    table.appendChild(tbody);
    form.appendChild(table);

    // Add submit button
    const submitButton = document.createElement("button");
    submitButton.type = "submit";
    submitButton.className =
      "w-full bg-indigo-600 text-white py-2 px-4 rounded hover:bg-indigo-700 transition";
    submitButton.textContent = "Submit Parameters";
    form.appendChild(submitButton);

    // Add form to container
    formContainer.appendChild(form);

    // Add form container to code output
    codeOutput.appendChild(formContainer);
    formContainer.scrollIntoView({ behavior: "smooth" });

  // Add event listener for form submission
    form.addEventListener("submit", function (e) {
      e.preventDefault();

      // Collect all parameter values
      const paramValues = [];
      cleanedQuestions.forEach((question, index) => {
        const input = form.querySelector(`input[name="param_${index}"]`);
        // Use the original question text for better context
        paramValues.push(`${question}: ${input.value}`);
      });

      // Join all parameter values into a single message
      const combinedMessage = paramValues.join(", ");

      // Remove the form
      formContainer.remove();

      // Display a message showing the submitted parameters
      const submittedMessage = document.createElement("div");
      submittedMessage.className = "message user";
      submittedMessage.innerHTML = `<strong>Submitted Parameters:</strong> ${combinedMessage}`;
      codeOutput.appendChild(submittedMessage);

      // Show typing indicator
      showTypingIndicator();

      // Send request to API using Server-Sent Events
      const params = new URLSearchParams({
        message: combinedMessage,
        is_edit_request: isEditMode.toString(),
      });
      const selectedPriority = getSelectedPriority();
      params.append("priority", selectedPriority.toString());
      console.log('[PRIORITY] Parameter follow-up SSE request with priority:', selectedPriority);

      if (currentSessionId) {
        params.append('session_id', currentSessionId);
      }

      const eventSource = new EventSource(`/api/generate-cad-stream?${params.toString()}`);

      eventSource.onmessage = function(event) {
        try {
          const data = JSON.parse(event.data);

          if (data.error) {
            throw new Error(data.error);
          }

          // Handle progress updates
          if (data.step) {
            console.log(`Progress: ${data.step} - ${data.status} (${data.overall_percentage}%)`);
            // You can add progress indicator updates here
          }

          // Handle final result
          if (data.final_result) {
            eventSource.close();
            // Reload chat history after successful completion
            setTimeout(loadChatHistory, 1000);
            // Process the final result as if it came from the old API
            // Remove typing indicator
            removeTypingIndicator();

            console.log("Server response:", data.final_result); // Log the response for debugging

            // Store session_id from response for continuity
            if (data.final_result.session_id) {
              updateSessionIndicator(data.final_result.session_id);
            }

            // ═══════════════════════════════════════════════════════════
            // UNIFIED RESPONSE RENDERING
            // Web content is now included in the message itself
            // No need to separately render web_search_metadata
            // ═══════════════════════════════════════════════════════════
            console.log("📋 URL info request:", data.final_result.is_url_info_request);
            
            // Check if this is a URL-based information request
            if (data.final_result.is_url_info_request) {
              console.log("🌐 URL-based information request - using unified message format");
            }

            // Check if there's a message asking for more parameters
            if (data.final_result.message) {
              console.log(
                "Received message from server after parameter submission:",
                data.final_result.message
              );

              // Display the message asking for more parameters
              displayMessage(data.final_result.message, "bot");

              // If there's an explanation, display it as well
              if (data.final_result.explanation) {
                  console.log(
                  "Received explanation from server after parameter submission:",
                  data.final_result.explanation
                );
                setTimeout(() => {
                  displayMessage(`<i>${data.final_result.explanation}</i>`, "bot-explanation");
                }, 500);
              }
            }
            // Display generated code if available
            else if (data.final_result.code) {
              displayCode(data.final_result.code);
              // Store the latest code
              latestCode = data.final_result.code;
            }
            // Display error if present
            else if (data.final_result.error) {
              displayError(data.final_result.error);
            }
            // Handle unexpected response format
            else {
              console.warn("Unexpected response format:", data.final_result);
              displayError(
                "Received an unexpected response format from the server."
              );
            }

            sendBtn.disabled = false;
          }
        } catch (error) {
          console.error("Error parsing SSE data:", error);
        }
      };

      eventSource.onerror = function(event) {
        console.error("Error with EventSource:", event);
        removeTypingIndicator();
        let errorMessage = "Sorry, an error occurred while connecting to the server.";
        if (event.message) {
            errorMessage += ` Details: ${event.message}`;
        } else if (event.target && event.target.readyState === EventSource.CLOSED) {
            // This can happen if the server closes the connection, which is normal after the stream is done.
            // We only show an error if it wasn't closed gracefully.
            if (!event.wasClean) {
                 errorMessage = "Connection to the server was closed unexpectedly.";
                 displayError(errorMessage);
            }
        } else {
            displayError(errorMessage);
        }
        sendBtn.disabled = false;
        eventSource.close();
      };
    });
    */
  }

  // Add CSS for messages and explanations
  const style = document.createElement("style");
  style.textContent = `
    .bot-explanation {
      font-size: 0.9em;
      color: #666;
      background-color: #f5f5f5;
      border-left: 3px solid #ccc;
      margin-left: 20px;
      padding: 5px 10px;
    }

    #code-output .message {
      margin-bottom: 10px;
      padding: 8px 12px;
      border-radius: 8px;
      max-width: 90%;
    }

    #code-output .message.bot {
      background-color: #e8f4ff !important;
      border-left: 3px solid #4299e1 !important;
      color: #2c5282 !important;
    }

    /* Parameter table styles */
    .parameter-form {
      box-shadow: 0 4px 15px rgba(0, 0, 0, 0.15);
      transition: all 0.3s ease;
      background-color: #f8faff;
      border: 2px solid #4299e1;
      animation: highlight-form 1s ease-in-out;
    }

    @keyframes highlight-form {
      0% { transform: scale(0.98); opacity: 0.8; }
      50% { transform: scale(1.02); opacity: 1; }
      100% { transform: scale(1); }
    }

    .parameter-form:hover {
      box-shadow: 0 6px 20px rgba(0, 0, 0, 0.18);
    }

    .parameter-form table {
      border-radius: 4px;
      overflow: hidden;
      width: 100%;
    }

    .parameter-form th {
      background-color: #4299e1;
      color: white;
      font-weight: 600;
      text-transform: uppercase;
      font-size: 0.85rem;
      letter-spacing: 0.05em;
    }

    .parameter-form input {
      border: 1px solid #cbd5e0;
      padding: 0.5rem 0.75rem;
      border-radius: 0.375rem;
      width: 100%;
      transition: all 0.2s;
    }

    .parameter-form input:focus {
      border-color: #4f46e5;
      box-shadow: 0 0 0 3px rgba(79, 70, 229, 0.25);
      outline: none;
    }

    .parameter-form button {
      font-weight: 600;
      transition: all 0.2s ease;
      text-transform: uppercase;
      letter-spacing: 0.05em;
      margin-top: 1rem;
    }

    .parameter-form button:hover {
      transform: translateY(-2px);
      box-shadow: 0 4px 8px rgba(0, 0, 0, 0.15);
    }
  `;
  document.head.appendChild(style);

  // Add CSS for notifications and drag & drop
  const enhancedStyles = document.createElement("style");
  enhancedStyles.textContent = `
    /* Enhanced drag and drop styles */
    .drag-over {
      background-color: #e8f4ff !important;
      border-color: #4299e1 !important;
      transform: scale(1.01);
      transition: all 0.3s ease;
      box-shadow: 0 8px 25px rgba(66, 153, 225, 0.15) !important;
    }

    /* Image preview styles */
    .image-preview-container {
      position: relative;
      transition: all 0.2s ease;
    }

    .image-preview-container:hover {
      transform: scale(1.05);
      box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);
    }

    .image-preview-container img {
      transition: transform 0.2s ease;
    }

    .image-preview-container:hover img {
      transform: scale(1.1);
    }

    /* Enhanced input area styling */
    #user-input {
      transition: all 0.3s ease;
    }

    #user-input:focus {
      outline: none;
    }

    /* File display animations */
    #selected-file-info > div {
      animation: slideInFromTop 0.3s ease-out;
    }

    @keyframes slideInFromTop {
      from {
        opacity: 0;
        transform: translateY(-10px);
      }
      to {
        opacity: 1;
        transform: translateY(0);
      }
    }

    /* Button hover effects */
    button {
      transition: all 0.2s ease;
    }

    button:hover {
      transform: translateY(-1px);
    }

    button:active {
      transform: translateY(0);
    }

    
    .bg-white.rounded-xl {
      transition: all 0.3s ease;
    }

    .bg-white.rounded-xl:hover {
      box-shadow: 0 8px 25px rgba(0, 0, 0, 0.06);
    }
  `;
  document.head.appendChild(enhancedStyles);

  // Load OBJ file button event listener
  if (loadObjBtn && objFileInput) {
    loadObjBtn.addEventListener("click", function () {
      console.log("CLIENT: Load OBJ button pressed");
      objFileInput.click();
    });

    // Handle file selection
    objFileInput.addEventListener("change", function (event) {
      const file = event.target.files[0];
      if (file && file.name.toLowerCase().endsWith('.obj')) {
        loadObjFile(file);
      } else {
        console.error("Please select a valid .obj file");
        alert("Please select a valid .obj file");
      }
    });
  }

  // Function to load OBJ file and display in 3D viewer
  function loadObjFile(file) {
    console.log("Loading OBJ file:", file.name);

    if (!modelViewer) {
      console.error("3D Model Viewer not initialized");
      alert("3D Model Viewer not available. Please refresh the page and try again.");
      return;
    }

    // Show loading state
    const originalContent = loadObjBtn.innerHTML;
    loadObjBtn.disabled = true;
    loadObjBtn.innerHTML = '<div class="loading-spinner-small mr-2"></div>Loading...';

    try {
      // Create a blob URL for the file
      const fileUrl = URL.createObjectURL(file);

      // Load the model using the ModelViewer3D
      modelViewer.loadModel(fileUrl, file.name)
        .then((success) => {
          if (success) {
            console.log("✅ OBJ file loaded successfully in 3D viewer");

            // Show success state
            loadObjBtn.innerHTML = '<i class="fas fa-check mr-2"></i>Loaded!';

            // Show code output section expanded
            const toggleBtn = document.getElementById("toggle-code-btn");
            const codeContainer = document.getElementById("code-output-container");
            if (toggleBtn && codeContainer) {
              codeContainer.classList.remove("hidden");
              const icon = toggleBtn.querySelector("i.fa-chevron-down");
              if (icon) icon.style.transform = "rotate(180deg)";
            }

            setTimeout(() => {
              loadObjBtn.innerHTML = originalContent;
              loadObjBtn.disabled = false;
            }, 2000);
          } else {
            throw new Error("Failed to load OBJ file in 3D viewer");
          }
        })
        .catch((error) => {
          console.error("❌ Error loading OBJ file:", error);

          // Show error state
          loadObjBtn.innerHTML = '<i class="fas fa-exclamation-triangle mr-2"></i>Error';

          setTimeout(() => {
            loadObjBtn.innerHTML = originalContent;
            loadObjBtn.disabled = false;
          }, 3000);

          alert(`Error loading OBJ file: ${error.message}`);
        });
    } catch (error) {
      console.error("Error creating file URL:", error);

      // Show error state
      loadObjBtn.innerHTML = '<i class="fas fa-exclamation-triangle mr-2"></i>Error';

      setTimeout(() => {
        loadObjBtn.innerHTML = originalContent;
        loadObjBtn.disabled = false;
      }, 3000);

      alert(`Error loading OBJ file: ${error.message}`);
    }
  }

  // Function to update session indicator
  function updateSessionIndicator(sessionId) {
    console.log(`📋 [SESSION DEBUG] Session updated: ${sessionId || "None"}`);

    if (sessionId) {
      currentSessionId = sessionId;
      if (sessionIdDisplay) {
        sessionIdDisplay.textContent = sessionId;
      }
      if (sessionIndicator) {
        sessionIndicator.classList.remove("hidden");
      }
      console.log("Session indicator updated:", sessionId);
    } else {
      if (sessionIndicator) {
        sessionIndicator.classList.add("hidden");
      }
      if (sessionIdDisplay) {
        sessionIdDisplay.textContent = "";
      }
      currentSessionId = null;
      sessionIndicator.onclick = null;
      console.log("❌ [SESSION DEBUG] Session indicator hidden");
    }
  }



  // Remove duplicate send button click handler - form submission handles this

  // Function to handle chat submission
  function handleChatSubmission() {
    console.log("handleChatSubmission called");
    const message = userInput ? userInput.value.trim() : "";

    // ✅ Guard: Nếu đang streaming thì chặn hoàn toàn, không cho submit
    if (isStreaming && !(awaitingConfirmReply && isShortConfirmReply(message))) {
      console.warn("⛔ [UI LOCK] Submission blocked - chatbot is currently streaming a response");
      return;
    }

    if (awaitingConfirmReply && isShortConfirmReply(message)) {
      markAwaitingConfirmReply(false, "confirm reply submitted");
      if (eventSource) {
        eventSource.close();
        eventSource = null;
      }
      setUILocked(false);
    }

    console.log("Message:", message, "Files:", {
      pdf: !!selectedPdfFile,
      image: !!selectedImageFile,
    });

    // Allow submission if there's either a message OR files selected
    if (!message && !selectedPdfFile && !selectedImageFile) {
      console.log("No message or files, returning");
      return;
    }

    // Hide welcome message when user starts any interaction
    hideWelcomeMessage();

    // Check if files are selected
    if (selectedPdfFile || selectedImageFile) {
      // Prepare attachment information for chat display
      const attachments = {};

      if (selectedImageFile) {
        attachments.image = {
          name: selectedImageFile.name,
          size: selectedImageFile.size,
          url: URL.createObjectURL(selectedImageFile),
        };
      }

      if (selectedPdfFile) {
        attachments.pdf = {
          name: selectedPdfFile.name,
          size: selectedPdfFile.size,
        };
      }

      // Add user message with attachments to chat
      console.log("Adding user message with attachments to chat");
      const attachmentCount =
        (attachments.image ? 1 : 0) + (attachments.pdf ? 1 : 0);
      const defaultMessage =
        attachmentCount > 1
          ? "📎 Multiple files attached"
          : "📎 File attachment";
      addChatMessage(message || defaultMessage, "user", null, attachments);

      // Show AI thinking message immediately after user message
      showAIThinkingMessage();

      // Determine which processing function to use
      if (selectedPdfFile && selectedImageFile) {
        // Process both files together
        processMultiFile(selectedPdfFile, selectedImageFile, message);
      } else if (selectedPdfFile) {
        // Process PDF only
        processPDF(selectedPdfFile, message);
      } else if (selectedImageFile) {
        // Process image only
        processImage(selectedImageFile, message);
      }

      // Reset file selections
      if (selectedFileInfo) selectedFileInfo.innerHTML = "";
      selectedPdfFile = null;
      selectedImageFile = null;

      // Clear input field
      if (userInput) {
        userInput.value = "";
        userInput.placeholder =
          "Enter CAD description or paste image (Ctrl+V), or drag and drop file here...";
        userInput.style.height = "auto";
      }
      if (charCount) charCount.textContent = "0";
      if (sendBtn) sendBtn.disabled = true;

      return; // Return early to avoid standard text processing
    }

    if (message) {
      if (!isShortConfirmReply(message)) {
        awaitingConfirmReply = false;
      }

      // Add user message to chat (display original clean message)
      console.log("Adding user message to chat");
      addChatMessage(message, "user");

      // Show AI thinking message immediately after user message
      showAIThinkingMessage();

      // Clear the input field immediately after getting the message
      userInput.value = "";
      userInput.style.height = "auto";
      if (charCount) charCount.textContent = "0";

      // Clear previous code output and hide results section if it's not for parameters
      if (codeOutput) codeOutput.innerHTML = "";
      const resultsSection = document
        .getElementById("code-output")
        ?.closest(".bg-white.rounded-xl");
      if (resultsSection) {
        resultsSection.classList.add("hidden");
      }

      // Show processing progress
      showProcessingProgress();
      // ✅ startRealtimeProgress sẽ tự gọi setUILocked(true) bên trong
      startRealtimeProgress(message, isEditMode, currentSessionId);
    }
  }

  // Add Enter key support for textarea
  if (userInput) {
    userInput.addEventListener("keydown", function (e) {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        // ✅ Cũng bị chặn bởi isStreaming guard trong handleChatSubmission
        if (!isStreaming || awaitingConfirmReply) {
          const hasContent = userInput.value.trim() !== "" || selectedPdfFile || selectedImageFile;
          if (hasContent) {
            handleChatSubmission();
          }
        }
      }
    });
  }

  // Handle file uploads
  function handleFileUpload(additionalText = "") {
    if (selectedPdfFile && selectedImageFile) {
      processMultiFile(selectedPdfFile, selectedImageFile, additionalText);
    } else if (selectedPdfFile) {
      processPDF(selectedPdfFile, additionalText);
    } else if (selectedImageFile) {
      processImage(selectedImageFile, additionalText);
    }
  }

  // Edit mode toggle handler
  if (editModeToggle) {
    editModeToggle.addEventListener("change", function () {
      isEditMode = this.checked;
      if (editModeIndicator) {
        if (isEditMode) {
          editModeIndicator.classList.remove("hidden");
          userInput.placeholder = "Enter edit request...";
        } else {
          editModeIndicator.classList.add("hidden");
          userInput.placeholder =
            "Describe your CAD model or ask a question...";
        }
      }
    });
  }

  // Quick action buttons
  const quickActionButtons = document.querySelectorAll(".quick-action-btn");
  quickActionButtons.forEach((button) => {
    button.addEventListener("click", function () {
      const action = this.dataset.action;
      let message = "";

      switch (action) {
        case "box":
          message = "Create a simple rectangular box";
          break;
        case "cylinder":
          message = "Create a cylinder";
          break;
        case "cone":
          message = "Create a cone";
          break;
      }

      if (message) {
        userInput.value = message;
        userInput.dispatchEvent(new Event("input"));
        userInput.focus();
      }
    });
  });

  // Initialize drag and drop
  initializeDragAndDrop();

  function initializeDragAndDrop() {
    if (!chatForm) return;

    // Prevent default drag behaviors
    ["dragenter", "dragover", "dragleave", "drop"].forEach((eventName) => {
      chatForm.addEventListener(eventName, preventDefaults, false);
      document.body.addEventListener(eventName, preventDefaults, false);
    });

    // Highlight drop area
    ["dragenter", "dragover"].forEach((eventName) => {
      chatForm.addEventListener(eventName, highlight, false);
    });

    ["dragleave", "drop"].forEach((eventName) => {
      chatForm.addEventListener(eventName, unhighlight, false);
    });

    // Handle dropped files
    chatForm.addEventListener("drop", handleDrop, false);

    function preventDefaults(e) {
      e.preventDefault();
      e.stopPropagation();
    }

    function highlight(e) {
      chatForm.classList.add("drag-over");
    }

    function unhighlight(e) {
      chatForm.classList.remove("drag-over");
    }

    function handleDrop(e) {
      const dt = e.dataTransfer;
      const files = dt.files;

      if (files.length > 0) {
        handleFiles(files);
      }
    }

    function handleFiles(files) {
      const file = files[0];
      const isPDF =
        file.type === "application/pdf" ||
        file.name.toLowerCase().endsWith(".pdf");
      const isImage =
        file.type.startsWith("image/") ||
        /\.(jpg|jpeg|png|gif|bmp|tiff|webp)$/i.test(file.name);

      if (isPDF) {
        selectedPdfFile = file;
      } else if (isImage) {
        selectedImageFile = file;
      } else {
        addChatMessage("Please select a valid PDF or image file.", "system");
        return;
      }

      updateFileDisplay();
      sendBtn.disabled = false;
    }
  }

  // Clipboard paste handler
  document.addEventListener("paste", function (e) {
    const items = e.clipboardData.items;

    for (let i = 0; i < items.length; i++) {
      if (items[i].type.indexOf("image") !== -1) {
        const blob = items[i].getAsFile();
        selectedImageFile = blob;
        updateFileDisplay();
        sendBtn.disabled = false;
        break;
      }
    }
  });



  // Refresh button handler
  if (refreshBtn) {
    refreshBtn.addEventListener("click", function () {
      // Clear chat messages except welcome message
      if (chatMessages) {
        const welcomeMessage = chatMessages.querySelector(
          ".chat-bubble.system"
        );
        chatMessages.innerHTML = "";
        if (welcomeMessage) {
          const welcomeContainer = document.createElement("div");
          welcomeContainer.className = "flex justify-center";
          welcomeContainer.appendChild(welcomeMessage);
          chatMessages.appendChild(welcomeContainer);
        } else {
          // Re-add welcome message
          const welcomeDiv = document.createElement("div");
          welcomeDiv.className = "flex justify-center";
          const bubbleDiv = document.createElement("div");
          bubbleDiv.className = "chat-bubble system p-3 max-w-md";
          bubbleDiv.innerHTML =
            '<i class="fas fa-sparkles mr-2"></i>Welcome! Describe a 3D model you\'d like to create, or upload an image/PDF for reference.';
          welcomeDiv.appendChild(bubbleDiv);
          chatMessages.appendChild(welcomeDiv);
        }
      }

      // Clear input and files
      if (userInput) {
        userInput.value = "";
        userInput.style.height = "auto";
        userInput.placeholder = "Describe your CAD model or ask a question...";
      }

      // Clear file selections
      selectedPdfFile = null;
      selectedImageFile = null;
      if (selectedFileInfo) {
        selectedFileInfo.innerHTML = "";
      }

      // Reset session
      currentSessionId = null;
      updateSessionIndicator(null);

      // Reset edit mode
      isEditMode = false;
      if (editModeToggle) {
        editModeToggle.checked = false;
      }
      if (editModeIndicator) {
        editModeIndicator.classList.add("hidden");
      }

      // Reset UI state
      if (sendBtn) sendBtn.disabled = true;
      if (charCount) charCount.textContent = "0";

      // Clear code output
      if (codeOutput) {
        codeOutput.innerHTML = `
          <div class="flex flex-col items-center justify-center h-full text-gray-400">
            <i class="fas fa-cube text-3xl mb-3"></i>
            <p class="text-center text-sm">Generated code and 3D model will appear here</p>
          </div>
        `;
      }

      // Hide view step button
      if (viewStepBtn) {
        viewStepBtn.classList.add("hidden");
      }

      showNotification("Chat cleared successfully", "success");
    });
  }

  // Suggestions toggle button handler
  const suggestionsToggle = document.getElementById('suggestions-toggle');
  if (suggestionsToggle) {
    suggestionsToggle.addEventListener('click', toggleSuggestions);
  }

  // Expose functions globally for testing
  window.processFiles = processFiles;
  window.processPDF = processPDF;
  window.processImage = processImage;
  window.processMultiFile = processMultiFile;
  window.toggleSuggestions = toggleSuggestions;

  // Function to load STEP viewer with 3D model
  function loadStepViewer(stepViewerUrl) {
    console.log("🎯 Loading STEP viewer with URL:", stepViewerUrl);

    // Try to get STEP viewer instance from different sources
    const stepViewer = window.stepViewer || window.dualViewer?.stepViewer;

    if (stepViewer && typeof stepViewer.loadModel === 'function') {
      console.log("✅ STEP viewer instance found, loading model...");

      // Extract filename from URL for display
      const fileName = stepViewerUrl.split('/').pop() || 'model.step';

      stepViewer.loadModel(stepViewerUrl, fileName)
        .then((success) => {
          if (success) {
            console.log("✅ 3D STEP model loaded successfully");
            // Show STEP viewer panel if it exists
            const stepPanel = document.getElementById('step-viewer-panel');
            if (stepPanel) {
              stepPanel.classList.remove('hidden');
            }
          } else {
            console.warn("⚠️ Failed to load 3D STEP model in viewer");
          }
        })
        .catch((error) => {
          console.error("❌ Error loading STEP model:", error);
        });
    } else {
      console.warn("⚠️ STEP viewer instance not available");
      console.log("Available viewers:", {
        stepViewer: !!window.stepViewer,
        dualViewer: !!window.dualViewer,
        dualViewerStepViewer: !!window.dualViewer?.stepViewer
      });
    }
  }

  // Expose loadStepViewer globally
  window.loadStepViewer = loadStepViewer;
});
