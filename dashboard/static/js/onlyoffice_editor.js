/**
 * Tareas - ONLYOFFICE Editor Integration
 * Oeffnet Dateien in einem neuen Browser-Tab
 */

// Editierbare Dateiformate (muss mit Backend uebereinstimmen)
const OO_EDITABLE_EXTENSIONS = new Set([
    'docx', 'doc', 'odt', 'txt', 'rtf', 'md', 'html',
    'xlsx', 'xls', 'ods', 'csv',
    'pptx', 'ppt', 'odp',
]);

/**
 * Pruefen ob ONLYOFFICE konfiguriert ist und das Dateiformat unterstuetzt wird.
 */
function isOnlyOfficeEditable(fileName, configured = false) {
    if (!configured || !fileName) return false;
    const ext = fileName.includes('.') ? fileName.split('.').pop().toLowerCase() : '';
    return OO_EDITABLE_EXTENSIONS.has(ext);
}

/**
 * ONLYOFFICE Editor in neuem Tab oeffnen.
 */
function openOnlyOfficeEditor(taskId, filePath) {
    const url = `/editor?taskId=${encodeURIComponent(taskId)}&path=${encodeURIComponent(filePath)}`;
    window.open(url, '_blank');
}
