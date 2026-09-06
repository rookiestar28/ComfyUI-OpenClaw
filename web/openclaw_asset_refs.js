const PREVIEWABLE_MEDIA_TYPES = new Set(["images", "video", "audio", "3d", "text"]);
const THREE_D_EXTENSIONS = [
    ".obj",
    ".fbx",
    ".gltf",
    ".glb",
    ".stl",
    ".ply",
    ".spz",
    ".splat",
    ".ksplat",
    ".usdz",
];
const HDR_IMAGE_EXTENSIONS = [".exr", ".hdr"];
const TEXT_PREVIEW_MAX_LENGTH = 1024;
const ADVANCED_3D_RESULT_MAX_ENTRIES = 8;
const ADVANCED_3D_RESULT_PATH_MAX_LENGTH = 1024;
const UNSAFE_ADVANCED_3D_PATH_CHARACTERS = /[\p{Cc}\p{Cf}\p{Cs}]/u;
const FILE_TEXT_EXTENSIONS = new Set(["txt", "md", "markdown", "json", "csv", "yaml", "yml", "xml", "log"]);
// The ComfyUI host directory vocabulary (`folder_paths` / `IO.FolderType`). One
// definition serves file-text refs and the Advanced 3D annotation so the two
// consumers cannot drift apart.
const HOST_DIRECTORY_TYPES = new Set(["input", "output", "temp"]);
const ADVANCED_3D_ANNOTATION_PATTERN = new RegExp(
    ` \\[(${[...HOST_DIRECTORY_TYPES].sort().join("|")})\\]$`,
);
const ADVANCED_3D_RESULT_WIRE_MAX_LENGTH = ADVANCED_3D_RESULT_PATH_MAX_LENGTH
    + Math.max(...[...HOST_DIRECTORY_TYPES].map((directoryType) => ` [${directoryType}]`.length));
const FILE_OUTPUT_MAX_REFS = 64;
const FILE_OUTPUT_FIELD_MAX_LENGTH = 1024;

function pickAssetHash(imageRef = {}) {
    if (!imageRef || typeof imageRef !== "object") {
        return "";
    }
    const direct = typeof imageRef.asset_hash === "string"
        ? imageRef.asset_hash.trim()
        : (typeof imageRef.hash === "string" ? imageRef.hash.trim() : "");
    if (direct) {
        return direct;
    }
    const nested = imageRef.asset;
    if (nested && typeof nested === "object") {
        if (typeof nested.asset_hash === "string" && nested.asset_hash.trim()) {
            return nested.asset_hash.trim();
        }
        if (typeof nested.hash === "string" && nested.hash.trim()) {
            return nested.hash.trim();
        }
    }
    return "";
}

function pickAssetApiId(imageRef = {}) {
    if (!imageRef || typeof imageRef !== "object") {
        return "";
    }
    const direct = typeof imageRef.asset_api_id === "string"
        ? imageRef.asset_api_id.trim()
        : (typeof imageRef.asset_id === "string" ? imageRef.asset_id.trim() : "");
    if (direct) {
        return direct;
    }
    const nested = imageRef.asset;
    if (!nested || typeof nested !== "object") {
        return "";
    }
    if (typeof nested.asset_id === "string" && nested.asset_id.trim()) {
        return nested.asset_id.trim();
    }
    if (typeof nested.id === "string" && nested.id.trim()) {
        return nested.id.trim();
    }
    return "";
}

function pickFilename(imageRef = {}) {
    if (!imageRef || typeof imageRef !== "object") {
        return "";
    }
    if (typeof imageRef.filename === "string" && imageRef.filename.trim()) {
        return imageRef.filename.trim();
    }
    if (typeof imageRef.name === "string" && imageRef.name.trim()) {
        return imageRef.name.trim();
    }
    return "";
}

function has3dExtension(filename = "") {
    return THREE_D_EXTENSIONS.some((ext) => String(filename).toLowerCase().endsWith(ext));
}

export function isHdrImageFilename(filename = "") {
    return HDR_IMAGE_EXTENSIONS.some((ext) => String(filename).toLowerCase().endsWith(ext));
}

function filenameFromUrl(url = "") {
    if (!url) {
        return "";
    }
    try {
        const base = typeof window !== "undefined" && window.location
            ? window.location.origin
            : "http://127.0.0.1";
        const parsed = new URL(url, base);
        return parsed.searchParams.get("filename") || parsed.pathname.split("/").pop() || "";
    } catch {
        return String(url).split("?", 1)[0].split("#", 1)[0].split("/").pop() || "";
    }
}

export function isHdrImageOutputRef(outputRef = {}) {
    if (!outputRef || typeof outputRef !== "object") {
        return false;
    }
    if (outputRef.media_type && outputRef.media_type !== "images") {
        return false;
    }
    if (isHdrImageFilename(outputRef.filename || "")) {
        return true;
    }
    return isHdrImageFilename(filenameFromUrl(outputRef.view_url || ""));
}

function resolveMediaType(imageRef = {}, fallback = "images") {
    if (imageRef && typeof imageRef === "object") {
        const direct = typeof imageRef.media_type === "string"
            ? imageRef.media_type.trim()
            : (typeof imageRef.mediaType === "string" ? imageRef.mediaType.trim() : "");
        if (PREVIEWABLE_MEDIA_TYPES.has(direct)) {
            return direct;
        }
    }
    return PREVIEWABLE_MEDIA_TYPES.has(fallback) ? fallback : "images";
}

function normalizeTextOutputRef(value) {
    if (value == null) {
        return null;
    }
    let content = String(value);
    if (!content) {
        return null;
    }
    const textTruncated = content.length > TEXT_PREVIEW_MAX_LENGTH;
    if (textTruncated) {
        content = content.slice(0, TEXT_PREVIEW_MAX_LENGTH);
    }
    return {
        filename: "",
        subfolder: "",
        type: "output",
        media_type: "text",
        asset_hash: "",
        asset_api_id: "",
        asset_api_required: false,
        resolution: "inline_text",
        unsupported_reason: "",
        is_asset_backed: false,
        content,
        text_truncated: textTruncated,
        viewParams: null,
    };
}

function hasUnsafeFileCharacters(value = "") {
    return Array.from(String(value)).some((char) => {
        const code = char.charCodeAt(0);
        return code < 32 || code === 127;
    });
}

function codePointLength(value = "") {
    return Array.from(String(value)).length;
}

function hasUnsafeAdvanced3dPathCharacters(value = "") {
    return UNSAFE_ADVANCED_3D_PATH_CHARACTERS.test(String(value));
}

function splitAdvanced3dAnnotation(rawPath) {
    // HOTSPOT: `PreviewUI3DAdvanced` reports `<path> [input|output|temp]`, so the
    // annotation must be separated *before* the 3D extension is validated.
    // Checking the extension first sees a name ending in `]` and drops every
    // current ComfyUI 3D preview. Everything after this split - length,
    // character, traversal, segment and extension checks - applies to the
    // canonical path only, and the returned type is always one of the
    // HOST_DIRECTORY_TYPES literals, never attacker-supplied text.
    //
    // Requiring the single ASCII separator is deliberately stricter than the
    // host's `folder_paths.annotated_filepath()`, which also accepts
    // `scene.glb[output]` and then truncates a real path character. Do not relax
    // the marker to match it.
    const match = ADVANCED_3D_ANNOTATION_PATTERN.exec(rawPath);
    if (!match) {
        return { canonicalPath: rawPath, directoryType: "output" };
    }
    return {
        canonicalPath: rawPath.slice(0, match.index),
        directoryType: match[1],
    };
}

function normalizeAdvanced3dResult(result) {
    if (
        !Array.isArray(result)
        || result.length === 0
        || result.length > ADVANCED_3D_RESULT_MAX_ENTRIES
    ) {
        return null;
    }

    const rawPath = result[0];
    if (
        typeof rawPath !== "string"
        || codePointLength(rawPath) > ADVANCED_3D_RESULT_WIRE_MAX_LENGTH
    ) {
        return null;
    }

    // HOTSPOT: see splitAdvanced3dAnnotation. The wire bound above admits the
    // longest annotation; the canonical bound below still guards the path.
    const { canonicalPath, directoryType } = splitAdvanced3dAnnotation(rawPath);

    const normalizedPath = canonicalPath.replaceAll("\\", "/");
    if (
        !normalizedPath
        || codePointLength(normalizedPath) > ADVANCED_3D_RESULT_PATH_MAX_LENGTH
        || hasUnsafeAdvanced3dPathCharacters(normalizedPath)
        || normalizedPath.startsWith("/")
        || [":", "%", "?", "#"].some((marker) => normalizedPath.includes(marker))
    ) {
        return null;
    }

    const segments = normalizedPath.split("/");
    if (segments.some((segment) => (
        !segment
        || segment === "."
        || segment === ".."
        || segment !== segment.trim()
    ))) {
        return null;
    }

    const filename = segments.at(-1);
    if (!has3dExtension(filename)) {
        return null;
    }

    // SECURITY: later result entries may contain private host metadata. Never
    // inspect or project anything except the validated path at index zero.
    return normalizeComfyOutputRef({
        filename,
        subfolder: segments.slice(0, -1).join("/"),
        type: directoryType,
    }, "3d");
}

function normalizeFileTextOutputRef(outputRef) {
    if (!outputRef || typeof outputRef !== "object" || Array.isArray(outputRef)) {
        return null;
    }

    if (typeof outputRef.filename !== "string") {
        return null;
    }
    if (codePointLength(outputRef.filename) > FILE_OUTPUT_FIELD_MAX_LENGTH) {
        return null;
    }
    const filename = outputRef.filename.trim();
    if (
        !filename
        || codePointLength(filename) > FILE_OUTPUT_FIELD_MAX_LENGTH
        || hasUnsafeFileCharacters(filename)
        || filename === "."
        || filename === ".."
        || filename.includes("/")
        || filename.includes("\\")
    ) {
        return null;
    }
    const dotIndex = filename.lastIndexOf(".");
    const suffix = dotIndex >= 0 ? filename.slice(dotIndex + 1).toLowerCase() : "";
    if (!FILE_TEXT_EXTENSIONS.has(suffix)) {
        return null;
    }

    if (outputRef.subfolder !== undefined && typeof outputRef.subfolder !== "string") {
        return null;
    }
    if (
        typeof outputRef.subfolder === "string"
        && codePointLength(outputRef.subfolder) > FILE_OUTPUT_FIELD_MAX_LENGTH
    ) {
        return null;
    }
    const subfolder = typeof outputRef.subfolder === "string" ? outputRef.subfolder.trim() : "";
    if (
        codePointLength(subfolder) > FILE_OUTPUT_FIELD_MAX_LENGTH
        || hasUnsafeFileCharacters(subfolder)
        || subfolder.includes("\\")
        || subfolder.startsWith("/")
        || subfolder.split("/").some((part) => part === "." || part === "..")
    ) {
        return null;
    }

    if (outputRef.type !== undefined && typeof outputRef.type !== "string") {
        return null;
    }
    const type = typeof outputRef.type === "string" && outputRef.type.trim()
        ? outputRef.type.trim()
        : "output";
    if (!HOST_DIRECTORY_TYPES.has(type)) {
        return null;
    }

    // SECURITY: accept only validated host fields and the existing encoded /view
    // seam. Raw URL or asset fields on a files entry are never fetch targets.
    return {
        filename,
        subfolder,
        type,
        media_type: "text",
        asset_hash: "",
        asset_api_id: "",
        asset_api_required: false,
        resolution: "view",
        unsupported_reason: "",
        is_asset_backed: false,
        content: "",
        text_truncated: false,
        viewParams: {
            filename,
            type,
            ...(subfolder ? { subfolder } : {}),
        },
    };
}

export function normalizeComfyOutputRef(imageRef = {}, mediaType = "images") {
    let outputRef = imageRef;
    const resolvedMediaType = resolveMediaType(outputRef, mediaType);

    if (!outputRef || typeof outputRef !== "object") {
        if (resolvedMediaType === "text") {
            return normalizeTextOutputRef(outputRef);
        }
        if (resolvedMediaType === "3d" && typeof outputRef === "string" && has3dExtension(outputRef)) {
            outputRef = { filename: outputRef, type: "output", subfolder: "" };
        } else {
            return null;
        }
    }

    const finalMediaType = resolveMediaType(outputRef, resolvedMediaType);
    const textContent = typeof outputRef.content === "string" && outputRef.content
        ? outputRef.content
        : (typeof outputRef.text === "string" && outputRef.text ? outputRef.text : "");
    if (finalMediaType === "text" && textContent) {
        return normalizeTextOutputRef(textContent);
    }

    const assetHash = pickAssetHash(outputRef);
    const assetApiId = pickAssetApiId(outputRef);
    const namedFilename = pickFilename(outputRef);
    const filename = namedFilename || assetHash || assetApiId;
    const subfolder = typeof outputRef.subfolder === "string" ? outputRef.subfolder : "";
    const type = typeof outputRef.type === "string" && outputRef.type ? outputRef.type : "output";

    if (!filename) {
        return null;
    }

    const explicitAssetApiRequired = outputRef.asset_api_required === true;
    const assetApiRequired = Boolean(explicitAssetApiRequired || (assetApiId && !assetHash && !namedFilename));

    // IMPORTANT: optional asset-hash metadata still resolves through /view when
    // hosts provide it; do not promote asset-api-only identifiers into implicit
    // /api/assets fetches.
    const viewParams = assetApiRequired
        ? null
        : (
            assetHash
                ? { filename: assetHash }
                : {
                    filename,
                    type,
                    ...(subfolder ? { subfolder } : {}),
                }
        );

    return {
        filename,
        subfolder,
        type,
        media_type: finalMediaType,
        asset_hash: assetHash || "",
        asset_api_id: assetApiId || "",
        asset_api_required: assetApiRequired,
        resolution: assetApiRequired ? "asset_api_required" : "view",
        unsupported_reason: assetApiRequired ? "asset_api_required" : "",
        is_asset_backed: Boolean(assetHash || assetApiId),
        content: "",
        text_truncated: false,
        viewParams,
    };
}

export function extractHistoryOutputRefs(historyItem = {}) {
    const results = [];
    const outputs = historyItem && typeof historyItem === "object" ? (historyItem.outputs || {}) : {};

    for (const nodeOutput of Object.values(outputs)) {
        if (!nodeOutput || typeof nodeOutput !== "object") {
            continue;
        }
        for (const [mediaType, refs] of Object.entries(nodeOutput)) {
            if (!PREVIEWABLE_MEDIA_TYPES.has(mediaType) || !Array.isArray(refs)) {
                continue;
            }
            for (const imageRef of refs) {
                const normalized = normalizeComfyOutputRef(imageRef, mediaType);
                if (normalized) {
                    results.push(normalized);
                }
            }
        }

        const advanced3dRef = normalizeAdvanced3dResult(nodeOutput.result);
        if (advanced3dRef) {
            results.push(advanced3dRef);
        }

        const fileRefs = nodeOutput.files;
        if (Array.isArray(fileRefs) && fileRefs.length <= FILE_OUTPUT_MAX_REFS) {
            for (const fileRef of fileRefs) {
                const normalized = normalizeFileTextOutputRef(fileRef);
                if (normalized) {
                    results.push(normalized);
                }
            }
        }
    }

    return results;
}

export function extractHistoryImageRefs(historyItem = {}) {
    return extractHistoryOutputRefs(historyItem).filter((ref) => ref.media_type === "images");
}
