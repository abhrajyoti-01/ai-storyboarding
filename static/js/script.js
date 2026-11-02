const form = document.getElementById("story-form");
const promptInput = document.getElementById("prompt");
const generateButton = document.getElementById("generate-btn");
const loadingBox = document.getElementById("loading");
const loadingText = document.getElementById("loading-text");
const progressBar = document.getElementById("progress-bar");
const progressFill = document.getElementById("progress-fill");
const loadingSteps = document.getElementById("loading-steps");
const errorBox = document.getElementById("error");
const resultSection = document.getElementById("result");
const titleNode = document.getElementById("story-title");
const panelsNode = document.getElementById("panels");

// Handle snippet chips
document.querySelectorAll(".snippet-chip").forEach(chip => {
  chip.addEventListener("click", () => {
    const prompt = chip.dataset.prompt;
    promptInput.value = prompt;
    
    // Update active state
    document.querySelectorAll(".snippet-chip").forEach(c => c.classList.remove("active"));
    chip.classList.add("active");
    
    // Focus textarea
    promptInput.focus();
  });
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  clearError();

  const prompt = promptInput.value.trim();
  if (prompt.length < 8) {
    showError("Please enter a slightly longer idea so the storyboard has enough detail.");
    return;
  }

  toggleLoading(true);
  hideResult();
  simulateProgress();

  try {
    // Create abort controller for timeout
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 180000); // 3 minute timeout

    console.log("Starting storyboard generation...");
    const startTime = Date.now();

    const response = await fetch("/generate", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ prompt }),
      signal: controller.signal,
    });

    clearTimeout(timeoutId);
    const elapsed = ((Date.now() - startTime) / 1000).toFixed(1);
    console.log(`Generation completed in ${elapsed}s`);
    completeProgress();

    const payload = await response.json().catch(() => ({}));

    if (!response.ok) {
      const message = typeof payload.error === "string" ? payload.error : "Generation failed.";
      throw new Error(message);
    }

    renderStoryboard(payload);
  } catch (error) {
    if (error.name === "AbortError") {
      showError("Request timed out. The server is taking too long. Please try again with a shorter prompt.");
    } else {
      console.error("Generation error:", error);
      const message = error instanceof Error ? error.message : "Unexpected error while generating storyboard.";
      showError(message);
    }
  } finally {
    toggleLoading(false);
    resetProgress();
  }
});

function toggleLoading(isLoading) {
  generateButton.disabled = isLoading;
  generateButton.textContent = isLoading ? "Generating..." : "Generate storyboard";
  loadingBox.classList.toggle("hidden", !isLoading);
}

function simulateProgress() {
  let progress = 0;
  const steps = loadingSteps.querySelectorAll(".step");
  
  // Reset steps
  steps.forEach(s => {
    s.classList.remove("active", "completed");
  });
  
  // Start first step
  if (steps[0]) steps[0].classList.add("active");
  
  // Simulate progress over time
  const interval = setInterval(() => {
    // Slower progress initially (writing phase)
    if (progress < 30) {
      progress += Math.random() * 2;
    } 
    // Medium progress (image generation phase)
    else if (progress < 75) {
      progress += Math.random() * 1.5;
      if (progress > 30 && steps[1]) {
        steps[0].classList.remove("active");
        steps[0].classList.add("completed");
        steps[1].classList.add("active");
      }
    } 
    // Finalizing phase
    else if (progress < 95) {
      progress += Math.random() * 2;
      if (progress > 75 && steps[2]) {
        steps[1].classList.remove("active");
        steps[1].classList.add("completed");
        steps[2].classList.add("active");
      }
    }
    
    progressFill.style.width = `${Math.min(progress, 95)}%`;
  }, 800);
  
  // Store interval ID to clear later
  window.progressInterval = interval;
}

function resetProgress() {
  if (window.progressInterval) {
    clearInterval(window.progressInterval);
  }
  progressFill.style.width = "0%";
  loadingSteps.querySelectorAll(".step").forEach(s => {
    s.classList.remove("active", "completed");
  });
}

function completeProgress() {
  if (window.progressInterval) {
    clearInterval(window.progressInterval);
  }
  progressFill.style.width = "100%";
  const steps = loadingSteps.querySelectorAll(".step");
  steps.forEach(s => {
    s.classList.remove("active");
    s.classList.add("completed");
  });
}

function renderStoryboard(data) {
  const panels = Array.isArray(data.panels) ? data.panels : [];
  if (panels.length === 0) {
    showError("No panels were returned. Please try again with a different idea.");
    return;
  }

  titleNode.textContent = typeof data.title === "string" && data.title.trim() ? data.title : "Storyboard";
  panelsNode.innerHTML = "";
  const imageQueue = [];

  panels.forEach((panel, index) => {
    const card = document.createElement("article");
    card.className = "panel-card";
    card.style.setProperty("--delay", `${index * 90}ms`);

    const imageContainer = document.createElement("div");
    imageContainer.className = "panel-image-container";

    const image = document.createElement("img");
    image.className = "panel-image";
    image.loading = "eager";
    image.alt = `Storyboard panel ${index + 1}`;
    image.style.opacity = "0";

    const processingOverlay = document.createElement("div");
    processingOverlay.className = "processing-overlay";
    processingOverlay.innerHTML = `
      <div class="processing-spinner"></div>
      <span class="processing-text">Processing...</span>
    `;

    const imageDataUrl = typeof panel.image_data_url === "string" ? panel.image_data_url : "";
    const hasEmbeddedImage = imageDataUrl.startsWith("data:image/");
    const imageUrl = typeof panel.image_url === "string" ? panel.image_url : "";
    const imageStatusMessage = typeof panel.image_status_message === "string"
      ? panel.image_status_message.trim()
      : "";

    // Show processing overlay immediately
    imageContainer.appendChild(image);
    imageContainer.appendChild(processingOverlay);

    const body = document.createElement("div");
    body.className = "panel-body";

    const meta = document.createElement("div");
    meta.className = "panel-meta";

    const number = document.createElement("p");
    number.className = "panel-number";
    number.textContent = `Panel ${panel.panel_number || index + 1}`;

    const status = document.createElement("span");
    status.className = "panel-status";
    if (hasEmbeddedImage) {
      status.textContent = imageStatusMessage || "⏳ Loading Gemini image...";
    } else if (imageUrl) {
      status.textContent = imageStatusMessage || "⏳ Generating image...";
    } else {
      status.textContent = "Image unavailable";
    }

    meta.appendChild(number);
    meta.appendChild(status);

    const scene = document.createElement("p");
    scene.className = "panel-scene";
    scene.textContent = typeof panel.scene_description === "string" ? panel.scene_description : "";

    const dialogue = document.createElement("p");
    dialogue.className = "panel-dialogue";
    dialogue.textContent = typeof panel.dialogue === "string" ? `\"${panel.dialogue}\"` : "";

    body.appendChild(meta);
    body.appendChild(scene);
    body.appendChild(dialogue);

    card.appendChild(imageContainer);
    card.appendChild(body);

    panelsNode.appendChild(card);

    if (hasEmbeddedImage) {
      loadImageFromSource(image, imageDataUrl, 120000).then(loaded => {
        if (loaded) {
          image.style.opacity = "1";
          processingOverlay.style.opacity = "0";
          processingOverlay.style.pointerEvents = "none";
          status.textContent = "✓";
          status.style.background = "#e0f5f7";
          status.style.color = "#2f9aa2";
        } else {
          processingOverlay.innerHTML = `
            <div class="processing-spinner" style="animation: none; border-top-color: #f26f3f;"></div>
            <span class="processing-text" style="color: #f26f3f;">✗ Failed</span>
          `;
          status.textContent = "✗ Image unavailable";
          status.style.background = "#fff0eb";
          status.style.color = "#f26f3f";
        }
      });
    } else if (imageUrl) {
      imageQueue.push({
        image,
        imageContainer,
        processingOverlay,
        status,
        imageUrl,
        scene: panel.scene_description || "Image failed to load",
      });
    }
  });

  resultSection.classList.remove("hidden");
  console.log(`Loading ${imageQueue.length} images via proxy...`);
  loadImagesInBatches(imageQueue, 1).catch(err => {
    console.error("Image loading failed:", err);
  });
}

function buildProxyUrl(remoteUrl) {
  return `/image-proxy?url=${encodeURIComponent(remoteUrl)}`;
}

async function loadImagesInBatches(imageQueue, batchSize) {
  for (let i = 0; i < imageQueue.length; i += batchSize) {
    const batch = imageQueue.slice(i, i + batchSize);
    await Promise.all(batch.map((item, batchIdx) => {
      const globalIdx = i + batchIdx;
      const total = imageQueue.length;
      console.log(`Loading image ${globalIdx + 1}/${total} via proxy`);
      item.status.textContent = `⏳ ${globalIdx + 1}/${total}...`;
      item.processingOverlay.querySelector('.processing-text').textContent = `${globalIdx + 1}/${total}...`;

      return loadPollinationsImage(item.image, item.imageUrl, 300000).then(loaded => {
        if (loaded) {
          item.image.style.opacity = "1";
          item.processingOverlay.style.opacity = "0";
          item.processingOverlay.style.pointerEvents = "none";
          item.status.textContent = "✓";
          item.status.style.background = "#e0f5f7";
          item.status.style.color = "#2f9aa2";
          console.log(`✓ Image ${globalIdx + 1} loaded`);
        } else {
          item.processingOverlay.innerHTML = `
            <div class="processing-spinner" style="animation: none; border-top-color: #f26f3f;"></div>
            <span class="processing-text" style="color: #f26f3f;">✗ Timeout</span>
          `;
          item.status.textContent = "✗ Timeout";
          item.status.style.background = "#fff0eb";
          item.status.style.color = "#f26f3f";
          console.error(`✗ Image ${globalIdx + 1} timed out`);
        }
      });
    }));
  }
}

async function loadPollinationsImage(image, imageUrl, timeoutMs) {
  const proxiedUrl = buildProxyUrl(imageUrl);
  return loadImageFromSource(image, proxiedUrl, timeoutMs);
}

async function loadImageFromSource(image, sourceUrl, timeoutMs) {
  return new Promise((resolve) => {
    let resolved = false;

    const timer = setTimeout(() => {
      if (!resolved) {
        resolved = true;
        image.removeEventListener("load", onLoad);
        image.removeEventListener("error", onError);
        resolve(false);
      }
    }, timeoutMs);

    const onLoad = () => {
      if (!resolved) {
        resolved = true;
        image.removeEventListener("load", onLoad);
        image.removeEventListener("error", onError);
        clearTimeout(timer);
        resolve(true);
      }
    };

    const onError = () => {
      if (!resolved) {
        resolved = true;
        image.removeEventListener("load", onLoad);
        image.removeEventListener("error", onError);
        clearTimeout(timer);
        resolve(false);
      }
    };

    image.addEventListener("load", onLoad);
    image.addEventListener("error", onError);
    image.src = sourceUrl;
  });
}

function wait(milliseconds) {
  return new Promise((resolve) => {
    setTimeout(resolve, milliseconds);
  });
}

function buildPlaceholderSvg(text) {
  const snippet = (text || "Image pending").substring(0, 120)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720">
    <defs><linearGradient id="bg" x1="0" x2="1" y1="0" y2="1">
      <stop offset="0%" stop-color="#f5efe6"/><stop offset="100%" stop-color="#f7d59c"/>
    </linearGradient></defs>
    <rect width="100%" height="100%" fill="url(#bg)"/>
    <rect x="64" y="64" width="1152" height="592" rx="24" fill="#ffffffaa" stroke="#1f2937" stroke-width="4"/>
    <text x="96" y="150" fill="#1f2937" font-size="42" font-family="Georgia, serif">Image Placeholder</text>
    <text x="96" y="220" fill="#374151" font-size="30" font-family="Georgia, serif">${snippet}</text>
  </svg>`;
  return "data:image/svg+xml;base64," + btoa(unescape(encodeURIComponent(svg)));
}

function clearError() {
  errorBox.textContent = "";
  errorBox.classList.add("hidden");
}

function showError(message) {
  errorBox.textContent = message;
  errorBox.classList.remove("hidden");
}

function hideResult() {
  resultSection.classList.add("hidden");
  panelsNode.innerHTML = "";
}
