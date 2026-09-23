// Brain Cell Counter v2.8.0 - headless QuPath analysis engine
//
// This script is launched by the Python v2 GUI. It expects ABBA atlas
// registration files to have already been exported into the QuPath entry.
// Atlas annotations are imported automatically when the ABBA ROI set and
// transform exist but the QuPath object hierarchy has not been loaded yet.
// It preserves atlas annotations during Cellpose and assigns each detection to
// one leaf Allen region only. If the requested hemisphere mode differs from
// the stored ABBA tree, that tree is rebuilt from the entry's ABBA ROI set.

import qupath.ext.biop.cellpose.Cellpose2D
import qupath.ext.biop.cellpose.CellposeSetup
import qupath.ext.biop.abba.AtlasTools
import qupath.lib.io.GsonTools
import qupath.lib.objects.PathObjects
import qupath.lib.regions.ImagePlane
import qupath.lib.roi.RoiTools
import qupath.lib.roi.ROIs
import java.nio.file.Files
import java.security.MessageDigest
import java.math.BigInteger
import java.util.Locale
import java.awt.BasicStroke
import java.awt.Color
import java.awt.Font
import java.awt.Rectangle
import java.awt.RenderingHints
import java.awt.geom.AffineTransform
import java.awt.image.BufferedImage
import javax.imageio.ImageIO


// -----------------------------------------------------------------------------
// Arguments
// -----------------------------------------------------------------------------

def parameters = [
        mode                : 'validate',
        neunChannel         : '1',
        cfosChannel         : '2',
        dapiChannel         : '3',
        neunThreshold       : '50',
        cfosThreshold       : '50',
        dapiThreshold       : '50',
        requireDapiThreshold: 'false',
        cellprobThreshold   : '-0.5',
        flowThreshold       : '0.4',
        cellposeModel       : 'cpsam',
        reuseDetections     : 'true',
        cellposePythonPath  : '',
        channel1Name        : 'NeuN',
        channel2Name        : 'c-Fos',
        channel3Name        : 'DAPI',
        hemisphereMode      : 'left-right',
        channel1ThresholdStart: '0',
        channel1ThresholdEnd: '200',
        channel1ThresholdStep: '5',
        channel2ThresholdStart: '0',
        channel2ThresholdEnd: '200',
        channel2ThresholdStep: '5',
        channel3ThresholdStart: '0',
        channel3ThresholdEnd: '200',
        channel3ThresholdStep: '5',
        thresholdPair       : 'channel1-channel2',
        functionalSubregionConfigPath: '',
        functionalSubregionConfigSha256: ''
]

args.each { argument ->
    int separator = argument.indexOf('=')
    if (separator > 0)
        parameters[argument.substring(0, separator)] = argument.substring(separator + 1)
}

String mode = parameters.mode.toLowerCase(Locale.ROOT)
int neunChannel = parameters.neunChannel as int
int cfosChannel = parameters.cfosChannel as int
int dapiChannel = parameters.dapiChannel as int
double neunThreshold = parameters.neunThreshold as double
double cfosThreshold = parameters.cfosThreshold as double
double dapiThreshold = parameters.dapiThreshold as double
boolean requireDapiThreshold = parameters.requireDapiThreshold.toBoolean()
double cellprobThreshold = parameters.cellprobThreshold as double
double flowThreshold = parameters.flowThreshold as double
String cellposeModel = parameters.cellposeModel?.trim() ?: 'cpsam'
if (!(cellposeModel in ['cpsam', 'cpsam_v2']))
    throw new IllegalArgumentException("Unsupported Cellpose model '${cellposeModel}'.")
boolean reuseDetections = parameters.reuseDetections.toBoolean()
String cellposePythonPath = parameters.cellposePythonPath
String channel1Name = parameters.channel1Name?.trim() ?: 'NeuN'
String channel2Name = parameters.channel2Name?.trim() ?: 'c-Fos'
String channel3Name = parameters.channel3Name?.trim() ?: 'DAPI'
String hemisphereMode = parameters.hemisphereMode?.trim()?.toLowerCase(Locale.ROOT) ?: 'left-right'
double channel1ThresholdStart = parameters.channel1ThresholdStart as double
double channel1ThresholdEnd = parameters.channel1ThresholdEnd as double
double channel1ThresholdStep = parameters.channel1ThresholdStep as double
double channel2ThresholdStart = parameters.channel2ThresholdStart as double
double channel2ThresholdEnd = parameters.channel2ThresholdEnd as double
double channel2ThresholdStep = parameters.channel2ThresholdStep as double
double channel3ThresholdStart = parameters.channel3ThresholdStart as double
double channel3ThresholdEnd = parameters.channel3ThresholdEnd as double
double channel3ThresholdStep = parameters.channel3ThresholdStep as double
String thresholdPair = parameters.thresholdPair?.trim()?.toLowerCase(Locale.ROOT) ?: 'channel1-channel2'
String functionalSubregionConfigPath = parameters.functionalSubregionConfigPath?.trim() ?: ''
String expectedFunctionalSubregionConfigSha256 = parameters.functionalSubregionConfigSha256?.trim()?.toLowerCase(Locale.ROOT) ?: ''

if (cellposePythonPath != null && !cellposePythonPath.trim().isEmpty()) {
    CellposeSetup.getInstance().setCellposePythonPath(cellposePythonPath)
    CellposeSetup.getInstance().setCellposeSAMPythonPath(cellposePythonPath)
}

final String ANALYSIS_ROI_NAME = '__BCC_ANALYSIS_ROI__'
final String CLASS_TRIPLE = "${channel3Name}+ ${channel1Name}+ ${channel2Name}+".toString()
final String CLASS_NEUN_ONLY = "${channel3Name}+ ${channel1Name}+ ${channel2Name}-".toString()
final String CLASS_CFOS_ONLY = "${channel3Name}+ ${channel1Name}- ${channel2Name}+".toString()
final String CLASS_DAPI_ONLY = "${channel3Name}+ ${channel1Name}- ${channel2Name}-".toString()
final String CLASS_OTHER = 'Other / below threshold'


def makeJsonSafe
makeJsonSafe = { value ->
    if (value instanceof GString)
        return value.toString()
    if (value instanceof Map)
        return value.collectEntries { key, nestedValue -> [(key): makeJsonSafe(nestedValue)] }
    if (value instanceof Collection)
        return value.collect { nestedValue -> makeJsonSafe(nestedValue) }
    return value
}

def event = { String kind, Object payload ->
    println 'BCC_EVENT ' + GsonTools.getInstance().toJson([
            kind: kind,
            payload: makeJsonSafe(payload)
    ])
}

def fail = { String message ->
    event('error', message)
    throw new IllegalStateException(message)
}

if (!(hemisphereMode in ['left-right', 'do-not-split']))
    fail("Invalid hemisphere mode '${hemisphereMode}'.")
if (!(thresholdPair in ['channel1-channel2', 'channel1-channel3', 'channel2-channel3', 'individual']))
    fail("Invalid threshold pair '${thresholdPair}'.")

def imageData = getCurrentImageData()
if (imageData == null)
    fail('QuPath did not open an image entry.')
event('progress', [percent: 4, stage: 'Opening image and project'])

def entry = getProjectEntry()
String imageName = entry?.getImageName() ?: imageData.getServer().getMetadata().getName()
int channelCount = imageData.getServer().nChannels()
if ([neunChannel, cfosChannel, dapiChannel].any { it < 1 || it > channelCount })
    fail("Invalid channel mapping for an image with ${channelCount} channels. GUI channel numbers are one-based.")


// -----------------------------------------------------------------------------
// Recover a damaged atlas tree, or rebuild it for the requested hemisphere mode
// -----------------------------------------------------------------------------

def annotationsBeforeRecovery = getAnnotationObjects()
def damagedAtlasRoot = annotationsBeforeRecovery.find { it.getName() == 'Root' }
boolean requestedSplitLeftRight = hemisphereMode == 'left-right'
boolean atlasRootMissing = damagedAtlasRoot == null
boolean atlasTreePresent = damagedAtlasRoot != null &&
        damagedAtlasRoot.getChildObjects().any { it.isAnnotation() }
boolean atlasTreeMissing = damagedAtlasRoot != null && !atlasTreePresent

// AtlasTools marks split annotations with a Left:/Right: class (and, depending
// on the ABBA version, sometimes also uses that prefix in the object name).
// A bilateral unsplit tree produces only one label centroid for both sides, so
// it must be rebuilt when the GUI requests Left and Right.
boolean storedAtlasIsSplit = atlasTreePresent && annotationsBeforeRecovery.any { annotation ->
    String classText = annotation.getPathClass()?.toString() ?: ''
    String objectText = annotation.getName() ?: ''
    classText.startsWith('Left:') || classText.startsWith('Right:') ||
            objectText.startsWith('Left:') || objectText.startsWith('Right:')
}
boolean atlasModeMismatch = atlasTreePresent && storedAtlasIsSplit != requestedSplitLeftRight
boolean atlasNeedsReload = atlasRootMissing || atlasTreeMissing || atlasModeMismatch
boolean atlasImportAttempted = false
boolean atlasImportSucceeded = false
String atlasImportError = ''

if (atlasNeedsReload) {
    def recoveryProject = getProject()
    File recoveryProjectDirectory = recoveryProject?.getPath()?.getParent()?.toFile()
    File recoveryOntologyFile = recoveryProjectDirectory?.listFiles()?.find { it.name.endsWith('-Ontology.json') }
    if (recoveryOntologyFile != null) {
        String ontologyName = recoveryOntologyFile.name.substring(
                0, recoveryOntologyFile.name.length() - '-Ontology.json'.length())
        try {
            atlasImportAttempted = true
            // Add the rebuilt atlas first. Only alter the previous Root after
            // successful recovery, so a failed attempt leaves it untouched.
            def recoveredRoot = AtlasTools.loadWarpedAtlasAnnotations(
                    imageData, ontologyName, 'id', requestedSplitLeftRight, false)
            if (recoveredRoot != null) {
                atlasImportSucceeded = true
                if (atlasRootMissing) {
                    event('log', "Imported ABBA atlas annotations from the exported registration (${requestedSplitLeftRight ? 'Left and Right' : 'Do not split'}).")
                } else if (atlasTreeMissing) {
                    // Legacy Cellpose runs sometimes replaced Root's atlas
                    // children with detections. Keep that ROI as the analysis
                    // parent so none of those detections are discarded.
                    damagedAtlasRoot.setName(ANALYSIS_ROI_NAME)
                    damagedAtlasRoot.setLocked(true)
                    event('log', "Recovered damaged ABBA atlas annotations from ROI set (${requestedSplitLeftRight ? 'Left and Right' : 'Do not split'}).")
                } else {
                    // Cellpose detections created by Brain Cell Counter live under the
                    // separate analysis ROI, so replacing this atlas subtree
                    // does not remove any detections.
                    imageData.getHierarchy().removeObject(damagedAtlasRoot, false)
                    event('log', "Rebuilt ABBA atlas annotations to match hemisphere mode: ${requestedSplitLeftRight ? 'Left and Right' : 'Do not split'}.")
                }
                fireHierarchyUpdate()
            }
        } catch (Throwable recoveryError) {
            atlasImportError = "${recoveryError.class.simpleName}: ${recoveryError.message}".toString()
            event('log', "Automatic ABBA import failed: ${atlasImportError}")
        }
    } else {
        atlasImportError = 'No *-Ontology.json file was found beside project.qpproj.'
    }
}


// -----------------------------------------------------------------------------
// Atlas hierarchy and ontology
// -----------------------------------------------------------------------------

def annotations = getAnnotationObjects()
def namedAtlasRoot = annotations.find { it.getName() == 'Root' }
def atlasRoot = namedAtlasRoot
if (atlasRoot == null && !annotations.isEmpty()) {
    // ABBA's root is normally named Root. The largest annotation is a safe
    // diagnostic fallback, but analysis refuses if it has no annotation tree.
    atlasRoot = annotations.max { it.getROI()?.getArea() ?: 0.0 }
}

def collectAtlasAnnotations
collectAtlasAnnotations = { object, List result ->
    object.getChildObjects().each { child ->
        if (child.isAnnotation()) {
            result << child
            collectAtlasAnnotations(child, result)
        }
    }
}

def atlasAnnotations = []
if (atlasRoot != null) {
    atlasAnnotations << atlasRoot
    collectAtlasAnnotations(atlasRoot, atlasAnnotations)
}
def leafRegions = atlasAnnotations.findAll { annotation ->
    annotation.getName() != ANALYSIS_ROI_NAME &&
            !annotation.getChildObjects().any { it.isAnnotation() }
}

def ontologyById = [:]
// Curated groupings are kept in a versioned external file so the biological
// definitions are reviewable without changing Allen's native ontology fields.
File functionalSubregionConfigFile = functionalSubregionConfigPath ?
        new File(functionalSubregionConfigPath) : null
if (functionalSubregionConfigFile == null || !functionalSubregionConfigFile.isFile())
    fail("Functional-subregion configuration is missing: ${functionalSubregionConfigPath}")
String functionalSubregionConfigSha256 = String.format('%064x', new BigInteger(1,
        MessageDigest.getInstance('SHA-256').digest(Files.readAllBytes(functionalSubregionConfigFile.toPath()))))
if (!expectedFunctionalSubregionConfigSha256 ||
        functionalSubregionConfigSha256 != expectedFunctionalSubregionConfigSha256)
    fail('Functional-subregion configuration SHA-256 does not match the analysis request.')
def functionalSubregionConfig = GsonTools.getInstance().fromJson(
        functionalSubregionConfigFile.getText('UTF-8'), Map.class)
String functionalSubregionSchema = functionalSubregionConfig.schema_version?.toString() ?: ''
String functionalSubregionConfigLabel = "${functionalSubregionSchema}:${functionalSubregionConfigSha256}"
def functionalSubregionGroups = (functionalSubregionConfig.groups ?: []).collect { group ->
    String groupName = group.name?.toString()?.trim()
    Set<String> rootAcronyms = (group.root_acronyms ?: []).collect {
        it?.toString()?.trim()
    }.findAll { it } as Set
    if (!groupName || rootAcronyms.isEmpty())
        fail('Every functional-subregion group requires a name and at least one root acronym.')
    return [name: groupName, roots: rootAcronyms]
}
if (!functionalSubregionSchema || functionalSubregionGroups.isEmpty())
    fail('Functional-subregion configuration has no schema version or groups.')
def duplicateGroupNames = functionalSubregionGroups.groupBy { it.name }.findAll { name, matches ->
    matches.size() > 1
}.keySet()
if (!duplicateGroupNames.isEmpty())
    fail("Functional-subregion configuration has duplicate names: ${duplicateGroupNames.sort().join(', ')}")
def functionalSubregionOverlaps = []
def project = getProject()
def ontologyFile = null
if (project != null) {
    File projectDirectory = project.getPath().getParent().toFile()
    ontologyFile = projectDirectory.listFiles()?.find { it.name.endsWith('-Ontology.json') }
    if (ontologyFile != null) {
        def ontology = GsonTools.getInstance().fromJson(ontologyFile.text, Map.class)
        def visitOntology
        visitOntology = { node, List ancestors ->
            // data.id is stored as a string in the ABBA ontology. Prefer it to
            // Gson's generic numeric node.id, which may otherwise become 997.0.
            String id = node.data?.id?.toString() ?: node.id?.toString()?.replaceFirst('\\.0$', '')
            def currentNode = [
                    acronym: node.data?.acronym?.toString() ?: id,
                    name   : node.data?.name?.toString() ?: id,
                    level  : node.data?.st_level?.toString() ?: ''
            ]
            def ontologyPath = ancestors + [currentNode]
            def majorNode = ontologyPath.find { it.level == '5' }
            if (majorNode == null) {
                majorNode = ontologyPath.find { it.level == '1' && it.acronym != 'grey' }
            }
            if (majorNode == null) {
                majorNode = ontologyPath.reverse().find { it.level != '0' && it.level != '1' }
            }
            String majorGroup = majorNode?.acronym == 'HPF' ? 'Hippocampus' :
                    (majorNode?.name ?: 'Unclassified')
            Set<String> ontologyAcronyms = ontologyPath.collect { it.acronym?.toString() }.findAll { it } as Set
            def matchingFunctionalGroups = functionalSubregionGroups.findAll { group ->
                !ontologyAcronyms.intersect(group.roots as Set).isEmpty()
            }
            if (matchingFunctionalGroups.size() > 1) {
                functionalSubregionOverlaps << [
                        acronym: currentNode.acronym,
                        groups: matchingFunctionalGroups.collect { it.name }
                ]
            }
            String functionalSubregion = matchingFunctionalGroups ? matchingFunctionalGroups[0].name : ''
            if (id != null) {
                ontologyById[id] = [
                        acronym  : currentNode.acronym,
                        name     : currentNode.name,
                        level    : currentNode.level,
                        majorGroup: majorGroup,
                        functionalSubregion: functionalSubregion
                ]
            }
            node.children?.each { visitOntology(it, ontologyPath) }
        }
        visitOntology(ontology.root, [])
        if (!functionalSubregionOverlaps.isEmpty()) {
            def firstOverlap = functionalSubregionOverlaps[0]
            fail("Functional-subregion groups overlap at ${firstOverlap.acronym}: ${firstOverlap.groups.join(', ')}")
        }
    }
}

boolean cellposeAvailable = true
try {
    Class.forName('qupath.ext.biop.cellpose.Cellpose2D')
} catch (Throwable ignored) {
    cellposeAvailable = false
}
String configuredCellposePython = CellposeSetup.getInstance().getCellposeSAMPythonPath()
boolean cellposePythonConfigured = configuredCellposePython != null &&
        !configuredCellposePython.trim().isEmpty() && new File(configuredCellposePython).isFile()

int ontologyMatchedLeafIds = leafRegions.count { region ->
    ontologyById.containsKey(region.getName()?.toString())
}
boolean atlasReady = namedAtlasRoot != null && !leafRegions.isEmpty() && ontologyFile != null &&
        ontologyMatchedLeafIds > 0

def validation = [
        version             : '2.8.0',
        image               : imageName,
        channels            : channelCount,
        annotations         : annotations.size(),
        atlasRootFound      : namedAtlasRoot != null,
        atlasAnnotations    : atlasAnnotations.size(),
        leafRegions         : leafRegions.size(),
        detections          : getDetectionObjects().size(),
        channelNames        : [channel1Name, channel2Name, channel3Name],
        hemisphereMode      : hemisphereMode,
        ontologyFound       : ontologyFile != null,
        ontologyEntries     : ontologyById.size(),
        majorGroups         : ontologyById.values().collect { it.majorGroup }.findAll { it }.unique().sort(),
        functionalSubregions: ontologyById.values().collect { it.functionalSubregion }.findAll { it }.unique().sort(),
        functionalSubregionSchema: functionalSubregionSchema,
        functionalSubregionConfigSha256: functionalSubregionConfigSha256,
        ontologyMatchedLeaves: ontologyMatchedLeafIds,
        atlasReady           : atlasReady,
        atlasImportAttempted : atlasImportAttempted,
        atlasImportSucceeded : atlasImportSucceeded,
        atlasImportError     : atlasImportError,
        cellposeAvailable   : cellposeAvailable,
        cellposeModel       : cellposeModel,
        cellposePythonPath  : configuredCellposePython,
        cellposePythonConfigured: cellposePythonConfigured,
        pixelWidthMicrons   : imageData.getServer().getPixelCalibration().getPixelWidthMicrons(),
        pixelHeightMicrons  : imageData.getServer().getPixelCalibration().getPixelHeightMicrons(),
        ready               : atlasReady && cellposeAvailable && cellposePythonConfigured
]
event('validation', validation)
event('progress', [percent: 12, stage: 'Validation complete'])

if (mode == 'apply-atlas') {
    if (!atlasReady) {
        String detail = atlasImportError?.trim()
        fail('ABBA atlas import is not ready for this entry. ' +
                (detail ? detail : 'Run ABBA/DeepSlice registration and export this slice to the QuPath project first.'))
    }
    def atlasResult = [
            mode                 : 'apply-atlas',
            image                : imageName,
            atlasAnnotations     : atlasAnnotations.size(),
            leafRegions          : leafRegions.size(),
            ontologyMatchedLeaves: ontologyMatchedLeafIds,
            hemisphereMode       : hemisphereMode,
            importedNow          : atlasImportSucceeded
    ]
    event('result', atlasResult)
    event('progress', [percent: 100, stage: 'ABBA atlas imported'])
    event('done', atlasResult)
    return
}

if (mode == 'validate') {
    event('progress', [percent: 100, stage: 'Validation complete'])
    event('done', validation)
    return
}

// -----------------------------------------------------------------------------
// Non-saving Cellpose parameter calibration on three representative crops
// -----------------------------------------------------------------------------

if (mode == 'cellpose-calibrate') {
    if (!validation.ready)
        fail('Entry is not ready for Cellpose parameter calibration.')
    def seedDetections = getDetectionObjects().findAll { it.getROI() != null }
    if (seedDetections.size() < 3)
        fail('At least three existing detections are required to choose representative calibration crops.')

    // Spread three crops across the tissue using detection x-position quartiles.
    // Calibration objects are created only in memory; this mode is never saved.
    def sortedSeeds = seedDetections.sort { it.getROI().getCentroidX() }
    def seedIndexes = [0.25, 0.50, 0.75].collect { fraction ->
        Math.min(sortedSeeds.size() - 1, Math.max(0, (int)Math.round((sortedSeeds.size() - 1) * fraction)))
    }
    double cropSize = 640.0
    double serverWidth = imageData.getServer().getWidth()
    double serverHeight = imageData.getServer().getHeight()
    def cropDefinitions = []
    seedIndexes.eachWithIndex { seedIndex, cropIndex ->
        def roi = sortedSeeds[seedIndex].getROI()
        double x = Math.max(0.0, Math.min(serverWidth - cropSize, roi.getCentroidX() - cropSize / 2.0))
        double y = Math.max(0.0, Math.min(serverHeight - cropSize, roi.getCentroidY() - cropSize / 2.0))
        cropDefinitions << [name: "Calibration ROI ${cropIndex + 1}", x: x, y: y, width: cropSize, height: cropSize]
    }

    def uniqueSorted = { Collection values ->
        values.collect { Math.round((it as double) * 1000.0) / 1000.0 }.unique().sort()
    }
    def cellprobCandidates = uniqueSorted([
            cellprobThreshold - 0.50,
            cellprobThreshold - 0.25,
            cellprobThreshold,
            cellprobThreshold + 0.25,
            cellprobThreshold + 0.50
    ])
    def flowCandidates = uniqueSorted([
            Math.max(0.05, flowThreshold - 0.10),
            flowThreshold,
            flowThreshold + 0.10
    ])
    int combinationCount = cellprobCandidates.size() * flowCandidates.size()
    int combinationIndex = 0
    def calibrationResults = []

    cellprobCandidates.each { candidateCellprob ->
        flowCandidates.each { candidateFlow ->
            combinationIndex++
            int percent = 15 + (int)(75.0 * (combinationIndex - 1) / Math.max(1, combinationCount))
            event('progress', [
                    percent: percent,
                    stage: "Calibrating Cellpose ${combinationIndex}/${combinationCount}: cellprob=${candidateCellprob}, flow=${candidateFlow}"
            ])
            def temporaryParents = cropDefinitions.collect { crop ->
                def cropROI = ROIs.createRectangleROI(
                        crop.x as double, crop.y as double, crop.width as double, crop.height as double,
                        ImagePlane.getDefaultPlane())
                def parent = PathObjects.createAnnotationObject(cropROI)
                parent.setName(crop.name)
                imageData.getHierarchy().getRootObject().addChildObject(parent)
                parent
            }
            try {
                def calibrationCellpose = Cellpose2D.builder(cellposeModel)
                        .channels(dapiChannel - 1)
                        .useCellposeSAM()
                        .normalizePercentilesGlobal(0.5, 99.5, 10)
                        .cellprobThreshold(candidateCellprob as double)
                        .flowThreshold(candidateFlow as double)
                        .measureShape()
                        .build()
                calibrationCellpose.detectObjects(imageData, temporaryParents)
                def countsByROI = temporaryParents.collect { parent ->
                    parent.getChildObjects().count { it.isDetection() }
                }
                def maskAreas = temporaryParents.collectMany { parent ->
                    parent.getChildObjects().findAll { it.isDetection() }.collect { detection ->
                        detection.getROI()?.getArea() ?: Double.NaN
                    }
                }.findAll { Double.isFinite(it as double) }.sort()
                double medianArea = maskAreas.isEmpty() ? Double.NaN :
                        maskAreas[(int)Math.floor(maskAreas.size() / 2)] as double
                calibrationResults << [
                        cellprob: candidateCellprob,
                        flow: candidateFlow,
                        countsByROI: countsByROI,
                        totalCount: countsByROI.sum() ?: 0,
                        medianMaskAreaPixels: medianArea
                ]
            } finally {
                imageData.getHierarchy().removeObjects(temporaryParents, false)
            }
        }
    }

    int maximumCount = calibrationResults.collect { it.totalCount as int }.max() ?: 0
    def viableResults = calibrationResults.findAll { result ->
        maximumCount > 0 && (result.totalCount as double) >= maximumCount * 0.50
    }
    if (viableResults.isEmpty())
        fail('Cellpose calibration produced no viable masks in the representative crops.')

    // Score local stability against neighboring grid points using each ROI
    // separately. This prevents a large crop from hiding instability elsewhere.
    viableResults.each { result ->
        def neighbors = calibrationResults.findAll { other ->
            boolean sameFlowAdjacentCellprob = other.flow == result.flow &&
                    Math.abs(cellprobCandidates.indexOf(other.cellprob) - cellprobCandidates.indexOf(result.cellprob)) == 1
            boolean sameCellprobAdjacentFlow = other.cellprob == result.cellprob &&
                    Math.abs(flowCandidates.indexOf(other.flow) - flowCandidates.indexOf(result.flow)) == 1
            sameFlowAdjacentCellprob || sameCellprobAdjacentFlow
        }
        def changes = neighbors.collectMany { neighbor ->
            (0..<result.countsByROI.size()).collect { roiIndex ->
                double reference = Math.max(1.0, result.countsByROI[roiIndex] as double)
                Math.abs((result.countsByROI[roiIndex] as double) -
                        (neighbor.countsByROI[roiIndex] as double)) / reference * 100.0
            }
        }
        result.stabilityScore = changes.isEmpty() ? Double.POSITIVE_INFINITY :
                changes.sum() / changes.size()
    }
    def recommendation = viableResults.min { left, right ->
        int comparison = (left.stabilityScore as double) <=> (right.stabilityScore as double)
        if (comparison != 0)
            return comparison
        comparison = (right.cellprob as double) <=> (left.cellprob as double)
        if (comparison != 0)
            return comparison
        (left.flow as double) <=> (right.flow as double)
    }

    def calibration = [
            version: '2.8.0',
            cellposeModel: cellposeModel,
            mode: 'cellpose-calibrate',
            image: imageName,
            roiCount: cropDefinitions.size(),
            cropSizePixels: cropSize,
            combinationsTested: combinationCount,
            candidates: calibrationResults,
            suggestedCellprob: recommendation.cellprob,
            suggestedFlow: recommendation.flow,
            stabilityScore: recommendation.stabilityScore,
            warning: 'Automatic heuristic suggestion; visually review masks before locking for the imaging batch.'
    ]
    event('progress', [percent: 100, stage: 'Cellpose parameter calibration complete'])
    event('done', calibration)
    return
}

// -----------------------------------------------------------------------------
// Fast per-section intensity profile for dataset-wide threshold optimization
// -----------------------------------------------------------------------------

if (mode == 'threshold-profile') {
    def profileROI = atlasRoot?.getROI()
    def profileDetections = getDetectionObjects().findAll { detection ->
        def roi = detection.getROI()
        roi != null && (profileROI == null || profileROI.contains(roi.getCentroidX(), roi.getCentroidY()))
    }
    if (profileDetections.isEmpty())
        fail('No existing detections were found. Run ANALYZE once for this section before threshold optimization.')

    String channel1Measurement = "Channel ${neunChannel}: Mean"
    String channel2Measurement = "Channel ${cfosChannel}: Mean"
    String channel3Measurement = "Channel ${dapiChannel}: Mean"
    def availableProfileMeasurements = profileDetections[0].getMeasurementList().getNames() as Set
    def requiredProfileMeasurements = [channel1Measurement, channel2Measurement, channel3Measurement]
    def missingProfileMeasurements = requiredProfileMeasurements.findAll {
        !availableProfileMeasurements.contains(it)
    }
    if (!missingProfileMeasurements.isEmpty())
        fail("Missing measurements required for threshold optimization: ${missingProfileMeasurements.join(', ')}")

    def buildThresholds = { double start, double end, double step, String label ->
        if (!Double.isFinite(start) || !Double.isFinite(end) || !Double.isFinite(step) ||
                step <= 0 || end < start)
            fail("Invalid ${label} threshold range. Require finite start/end, end >= start and step > 0.")
        int count = (int)Math.floor((end - start) / step + 1.0e-9) + 1
        if (count < 2)
            fail("${label} threshold range must contain at least two values.")
        if (count > 1001)
            fail("${label} threshold range contains ${count} values; maximum is 1001.")
        (0..<count).collect { index ->
            // Remove floating-point noise so JSON/CSV thresholds remain readable.
            Math.round((start + index * step) * 1.0e9) / 1.0e9
        }
    }

    def channel1Thresholds = buildThresholds(
            channel1ThresholdStart, channel1ThresholdEnd, channel1ThresholdStep, 'Channel 1')
    def channel2Thresholds = buildThresholds(
            channel2ThresholdStart, channel2ThresholdEnd, channel2ThresholdStep, 'Channel 2')
    def channel3Thresholds = buildThresholds(
            channel3ThresholdStart, channel3ThresholdEnd, channel3ThresholdStep, 'Channel 3')
    event('progress', [percent: 35, stage: "Reading intensity measurements from ${profileDetections.size()} detections"])

    def channel1Values = []
    def channel2Values = []
    def channel3Values = []
    def pairedThresholdValues = []
    int missingFiniteIntensity = 0
    profileDetections.each { detection ->
        def measurements = detection.getMeasurementList()
        double channel1Value = measurements.get(channel1Measurement)
        double channel2Value = measurements.get(channel2Measurement)
        double channel3Value = measurements.get(channel3Measurement)
        if (Double.isFinite(channel1Value))
            channel1Values << channel1Value
        else
            missingFiniteIntensity++
        if (Double.isFinite(channel2Value))
            channel2Values << channel2Value
        else
            missingFiniteIntensity++
        if (Double.isFinite(channel3Value))
            channel3Values << channel3Value
        else
            missingFiniteIntensity++
        if (thresholdPair == 'channel1-channel2' && Double.isFinite(channel1Value) && Double.isFinite(channel2Value))
            pairedThresholdValues << [channel1Value, channel2Value]
        else if (thresholdPair == 'channel1-channel3' && Double.isFinite(channel1Value) && Double.isFinite(channel3Value))
            pairedThresholdValues << [channel1Value, channel3Value]
        else if (thresholdPair == 'channel2-channel3' && Double.isFinite(channel2Value) && Double.isFinite(channel3Value))
            pairedThresholdValues << [channel2Value, channel3Value]
    }

    // Sorted arrays plus binary search make a wide threshold scan much faster
    // than rechecking every detection for every candidate threshold.
    channel1Values.sort()
    channel2Values.sort()
    channel3Values.sort()
    def countAtOrAbove = { List<Double> sortedValues, double threshold ->
        int low = 0
        int high = sortedValues.size()
        while (low < high) {
            int middle = (low + high) >>> 1
            if (sortedValues[middle] < threshold)
                low = middle + 1
            else
                high = middle
        }
        sortedValues.size() - low
    }
    def channel1Counts = channel1Thresholds.collect { threshold ->
        countAtOrAbove(channel1Values, threshold as double)
    }
    event('progress', [percent: 58, stage: 'Scanning Channel 1 thresholds'])
    def channel2Counts = channel2Thresholds.collect { threshold ->
        countAtOrAbove(channel2Values, threshold as double)
    }
    event('progress', [percent: 76, stage: 'Scanning Channel 2 thresholds'])
    def channel3Counts = channel3Thresholds.collect { threshold ->
        countAtOrAbove(channel3Values, threshold as double)
    }
    event('progress', [percent: 92, stage: 'Scanning Channel 3 thresholds'])

    // Build the selected two-channel double-positive grid efficiently.
    // Each detection is placed in one 2-D intensity bin, followed by an in-place
    // suffix sum. This avoids rescanning every detection for every threshold pair.
    def thresholdByKey = [
            channel1: channel1Thresholds,
            channel2: channel2Thresholds,
            channel3: channel3Thresholds
    ]
    def pairKeys = thresholdPair == 'individual' ? [] : thresholdPair.split('-').toList()
    def firstPairThresholds = pairKeys ? thresholdByKey[pairKeys[0]] : []
    def secondPairThresholds = pairKeys ? thresholdByKey[pairKeys[1]] : []
    int jointRows = firstPairThresholds.size()
    int jointColumns = secondPairThresholds.size()
    if ((long)jointRows * (long)jointColumns > 25000L)
        fail("${pairKeys.join(' + ')} creates ${jointRows * jointColumns} threshold pairs; maximum is 25000. Increase one or both step sizes.")
    int[][] doublePositiveCounts = new int[jointRows][jointColumns]
    def upperBound = { List<Double> thresholds, double value ->
        int low = 0
        int high = thresholds.size()
        while (low < high) {
            int middle = (low + high) >>> 1
            if (thresholds[middle] <= value)
                low = middle + 1
            else
                high = middle
        }
        low
    }
    pairedThresholdValues.each { pair ->
        int row = upperBound(firstPairThresholds, pair[0] as double) - 1
        int column = upperBound(secondPairThresholds, pair[1] as double) - 1
        if (row >= 0 && column >= 0)
            doublePositiveCounts[row][column]++
    }
    for (int row = jointRows - 1; row >= 0; row--) {
        for (int column = jointColumns - 1; column >= 0; column--) {
            int below = row + 1 < jointRows ? doublePositiveCounts[row + 1][column] : 0
            int right = column + 1 < jointColumns ? doublePositiveCounts[row][column + 1] : 0
            int diagonal = row + 1 < jointRows && column + 1 < jointColumns ?
                    doublePositiveCounts[row + 1][column + 1] : 0
            doublePositiveCounts[row][column] += below + right - diagonal
        }
    }
    if (pairKeys)
        event('progress', [percent: 96, stage: "Calculating ${pairKeys.join(' + ')} double-positive threshold grid"])

    def profile = [
            version             : '2.8.0',
            cellposeModel       : cellposeModel,
            mode                : 'threshold-profile',
            image               : imageName,
            detections          : profileDetections.size(),
            missingFiniteIntensity: missingFiniteIntensity,
            channels            : [
                    [key: 'channel1', name: 'Channel 1', measurement: channel1Measurement,
                     thresholds: channel1Thresholds, counts: channel1Counts,
                     finiteValues: channel1Values.size()],
                    [key: 'channel2', name: 'Channel 2', measurement: channel2Measurement,
                     thresholds: channel2Thresholds, counts: channel2Counts,
                     finiteValues: channel2Values.size()],
                    [key: 'channel3', name: 'Channel 3', measurement: channel3Measurement,
                     thresholds: channel3Thresholds, counts: channel3Counts,
                     finiteValues: channel3Values.size()]
            ],
            doublePositive      : pairKeys ? [
                    firstChannelKey : pairKeys[0],
                    secondChannelKey: pairKeys[1],
                    firstThresholds : firstPairThresholds,
                    secondThresholds: secondPairThresholds,
                    counts: doublePositiveCounts.collect { row -> row.toList() },
                    finitePairs: pairedThresholdValues.size()
            ] : null
    ]
    event('threshold_profile', profile)
    event('progress', [percent: 100, stage: 'Threshold profile complete'])
    event('done', profile)
    return
}

if (mode == 'cellpose-smoke') {
    if (!validation.ready)
        fail('Entry is not ready for a Cellpose smoke test.')
    def smokeParent = leafRegions.min { it.getROI().getArea() }
    event('log', "Running non-saving Cellpose smoke test in leaf region ${smokeParent.getName()}.")
    event('progress', [percent: 18, stage: 'Starting Cellpose smoke test'])
    try {
        def smokeCellpose = Cellpose2D.builder(cellposeModel)
                .channels(dapiChannel - 1)
                .useCellposeSAM()
                .normalizePercentilesGlobal(0.5, 99.5, 10)
                .cellprobThreshold(cellprobThreshold)
                .flowThreshold(flowThreshold)
                .measureShape()
                .measureIntensity()
                .build()
        int before = getDetectionObjects().size()
        smokeCellpose.detectObjects(imageData, [smokeParent])
        event('progress', [percent: 92, stage: 'Cellpose smoke test complete'])
        int created = getDetectionObjects().size() - before
        event('done', [mode: 'cellpose-smoke', cellposeModel: cellposeModel,
                       regionId: smokeParent.getName(), detectionsCreated: created])
        return
    } catch (Throwable error) {
        fail("Cellpose smoke test failed: ${error.class.simpleName}: ${error.message}")
    }
}
if (mode != 'analyze')
    fail("Unknown mode '${mode}'. Expected validate, cellpose-calibrate, threshold-profile, cellpose-smoke or analyze.")
if (!validation.ready)
    fail('Entry is not ready. Load ABBA atlas annotations and verify the Cellpose extension before analysis.')
event('progress', [percent: 15, stage: 'Preparing analysis'])


// -----------------------------------------------------------------------------
// Cellpose parent and detection population
// -----------------------------------------------------------------------------

// Never run Cellpose directly on the atlas Root: Cellpose replaces children of
// its parent, which would destroy the Allen annotation tree. Use a separate,
// root-level analysis annotation with the same ROI.
def analysisParent = annotations.find { it.getName() == ANALYSIS_ROI_NAME }
if (analysisParent == null) {
    analysisParent = PathObjects.createAnnotationObject(atlasRoot.getROI())
    analysisParent.setName(ANALYSIS_ROI_NAME)
    imageData.getHierarchy().getRootObject().addChildObject(analysisParent)
    fireHierarchyUpdate()
}
def parentROI = analysisParent.getROI()
double hemisphereMidlineX = parentROI.getBoundsX() + parentROI.getBoundsWidth() / 2.0
def detections = getDetectionObjects().findAll { detection ->
    def roi = detection.getROI()
    roi != null && parentROI.contains(roi.getCentroidX(), roi.getCentroidY())
}
boolean reused = !detections.isEmpty()

if (reused && !reuseDetections)
    fail("Found ${detections.size()} existing detections. Enable reuse or remove them deliberately in QuPath.")

if (!reused) {
    event('log', "Running Cellpose-SAM model ${cellposeModel} on ABBA Root ROI; DAPI channel ${dapiChannel} (GUI one-based).")
    event('progress', [percent: 18, stage: 'Preparing Cellpose tiles'])
    def cellpose = Cellpose2D.builder(cellposeModel)
            .channels(dapiChannel - 1)
            .useCellposeSAM()
            .normalizePercentilesGlobal(0.5, 99.5, 10)
            .cellprobThreshold(cellprobThreshold)
            .flowThreshold(flowThreshold)
            .measureShape()
            .measureIntensity()
            .build()
    cellpose.detectObjects(imageData, [analysisParent])
    event('progress', [percent: 76, stage: 'Cellpose detections imported'])
    detections = getDetectionObjects().findAll { detection ->
        def roi = detection.getROI()
        roi != null && parentROI.contains(roi.getCentroidX(), roi.getCentroidY())
    }
} else {
    event('progress', [percent: 76, stage: "Reusing ${detections.size()} detections"])
}

if (detections.isEmpty())
    fail('Cellpose returned no detections inside the ABBA Root ROI.')

// Existing QuPath detections predate model provenance metadata. Never label
// reused masks as though the newly selected model created them.
String detectionModelLabel = reused ? 'existing detections (unknown model)' : cellposeModel

String neunMeasurement = "Channel ${neunChannel}: Mean"
String cfosMeasurement = "Channel ${cfosChannel}: Mean"
String dapiMeasurement = "Channel ${dapiChannel}: Mean"
def requiredMeasurements = [neunMeasurement, cfosMeasurement]
if (requireDapiThreshold)
    requiredMeasurements << dapiMeasurement
def availableMeasurements = detections[0].getMeasurementList().getNames() as Set
def missingMeasurements = requiredMeasurements.findAll { !availableMeasurements.contains(it) }
if (!missingMeasurements.isEmpty())
    fail("Missing measurements: ${missingMeasurements.join(', ')}")


// -----------------------------------------------------------------------------
// Classification
// -----------------------------------------------------------------------------

def counts = [total: detections.size(), dapi: 0, neun: 0, cfos: 0, doublePositive: 0, triple: 0, other: 0]
event('progress', [percent: 78, stage: "Classifying ${detections.size()} detections"])
detections.each { detection ->
    def measurements = detection.getMeasurementList()
    double neun = measurements.get(neunMeasurement)
    double cfos = measurements.get(cfosMeasurement)
    double dapi = requireDapiThreshold ? measurements.get(dapiMeasurement) : Double.NaN
    boolean markersFinite = Double.isFinite(neun) && Double.isFinite(cfos)
    boolean dapiFinite = !requireDapiThreshold || Double.isFinite(dapi)
    boolean isDapi = markersFinite && dapiFinite && (!requireDapiThreshold || dapi >= dapiThreshold)
    boolean isNeuN = markersFinite && neun >= neunThreshold
    boolean isCFos = markersFinite && cfos >= cfosThreshold
    if (isNeuN && isCFos)
        counts.doublePositive++

    String pathClass
    if (!isDapi) {
        pathClass = CLASS_OTHER
        counts.other++
    } else if (isNeuN && isCFos) {
        pathClass = CLASS_TRIPLE
        counts.dapi++; counts.neun++; counts.cfos++; counts.triple++
    } else if (isNeuN) {
        pathClass = CLASS_NEUN_ONLY
        counts.dapi++; counts.neun++
    } else if (isCFos) {
        pathClass = CLASS_CFOS_ONLY
        counts.dapi++; counts.cfos++
    } else {
        pathClass = CLASS_DAPI_ONLY
        counts.dapi++
    }
    detection.setPathClass(getPathClass(pathClass))
}
fireHierarchyUpdate()
event('progress', [percent: 84, stage: 'Cell classification complete'])


// -----------------------------------------------------------------------------
// Mutually exclusive leaf-region assignment and CSV export
// -----------------------------------------------------------------------------

def regionsByArea = leafRegions.sort { a, b ->
    a.getROI().getArea() <=> b.getROI().getArea()
}
double pixelWidthMicrons = imageData.getServer().getPixelCalibration().getPixelWidthMicrons()
double pixelHeightMicrons = imageData.getServer().getPixelCalibration().getPixelHeightMicrons()
boolean physicalCalibrationValid = Double.isFinite(pixelWidthMicrons) && pixelWidthMicrons > 0 &&
        Double.isFinite(pixelHeightMicrons) && pixelHeightMicrons > 0
def rows = [:]

def hemisphereForRegion = { region ->
    if (hemisphereMode == 'do-not-split')
        return 'Combined'
    String className = region.getPathClass()?.toString() ?: ''
    String objectName = region.getName()?.toString() ?: ''
    if (className.startsWith('Left:') || objectName.startsWith('Left:'))
        return 'Left'
    if (className.startsWith('Right:') || objectName.startsWith('Right:'))
        return 'Right'
    return region.getROI().getCentroidX() < hemisphereMidlineX ? 'Left' : 'Right'
}

def ensureRegionRow = { String regionId, String hemisphere ->
    String key = "${regionId}|${hemisphere}"
    if (!rows.containsKey(key)) {
        def ontology = ontologyById[regionId] ?: [
                acronym: regionId,
                name: regionId,
                level: '',
                majorGroup: 'Unclassified',
                functionalSubregion: ''
        ]
        rows[key] = [
                regionId: regionId,
                acronym: ontology.acronym,
                regionName: ontology.name,
                majorGroup: ontology.majorGroup,
                functionalSubregion: ontology.functionalSubregion ?: '',
                hierarchyLevel: ontology.level,
                hemisphere: hemisphere,
                regionAreaMm2: physicalCalibrationValid ? 0.0 : null,
                dapi: 0,
                neun: 0,
                cfos: 0,
                doublePositive: 0,
                triple: 0
        ]
    }
    return rows[key]
}

// Seed every atlas leaf, including regions with zero detected objects. This
// makes area and density comparisons complete instead of silently omitting
// zero-count regions. Duplicate pieces are merged by region and hemisphere.
leafRegions.each { region ->
    String regionId = region.getName()?.toString() ?: ''
    if (!regionId)
        return
    def row = ensureRegionRow(regionId, hemisphereForRegion(region))
    if (physicalCalibrationValid && region.getROI() != null) {
        row.regionAreaMm2 += region.getROI().getArea() * pixelWidthMicrons * pixelHeightMicrons / 1_000_000.0
    }
}

int unassigned = 0
int regionProgressStep = Math.max(1, (int)(detections.size() / 10))
event('progress', [percent: 86, stage: 'Assigning cells to Allen regions'])

detections.eachWithIndex { detection, detectionIndex ->
    if (detectionIndex % regionProgressStep == 0) {
        int percent = 86 + (int)(8.0 * detectionIndex / Math.max(1, detections.size()))
        event('progress', [
                percent: percent,
                stage: "Assigning regions ${detectionIndex}/${detections.size()}"
        ])
    }
    double x = detection.getROI().getCentroidX()
    double y = detection.getROI().getCentroidY()
    def region = regionsByArea.find { it.getROI().contains(x, y) }
    if (region == null) {
        unassigned++
        return
    }

    String regionId = region.getName()?.toString() ?: ''
    String className = region.getPathClass()?.toString() ?: ''
    String hemisphere
    if (hemisphereMode == 'do-not-split') {
        hemisphere = 'Combined'
    } else if (className.startsWith('Left:')) {
        hemisphere = 'Left'
    } else if (className.startsWith('Right:')) {
        hemisphere = 'Right'
    } else {
        // ABBA "Do not split" regions contain both sides in one ROI. Split
        // their detections at the geometric midline of the transformed Root.
        hemisphere = x < hemisphereMidlineX ? 'Left' : 'Right'
    }
    // Merge duplicate pieces of one Allen region within each requested output
    // hemisphere. In do-not-split mode this also combines both sides.
    def row = ensureRegionRow(regionId, hemisphere)
    String cellClass = detection.getPathClass()?.toString()
    def measurements = detection.getMeasurementList()
    double regionNeuN = measurements.get(neunMeasurement)
    double regionCFos = measurements.get(cfosMeasurement)
    boolean isDoublePositive = Double.isFinite(regionNeuN) && Double.isFinite(regionCFos) &&
            regionNeuN >= neunThreshold && regionCFos >= cfosThreshold
    if (cellClass != CLASS_OTHER) row.dapi++
    if (cellClass == CLASS_TRIPLE || cellClass == CLASS_NEUN_ONLY) row.neun++
    if (cellClass == CLASS_TRIPLE || cellClass == CLASS_CFOS_ONLY) row.cfos++
    if (isDoublePositive) row.doublePositive++
    if (cellClass == CLASS_TRIPLE) row.triple++
}

def csvEscape = { value ->
    String text = value == null ? '' : value.toString()
    if (text.contains(',') || text.contains('"') || text.contains('\n') || text.contains('\r'))
        return '"' + text.replace('"', '""') + '"'
    return text
}

def csvHeaderName = { String value ->
    value.replaceAll('[^A-Za-z0-9]+', '_').replaceAll('^_+|_+$', '') ?: 'Channel'
}

def formatMetric = { value, int decimals ->
    if (value == null || !(value instanceof Number) || !Double.isFinite((value as Number).doubleValue()))
        return ''
    return String.format(Locale.US, "%.${decimals}f", (value as Number).doubleValue())
}

def densityPerMm2 = { int count, areaMm2 ->
    areaMm2 instanceof Number && (areaMm2 as Number).doubleValue() > 0 ?
            count / (areaMm2 as Number).doubleValue() : null
}

def percentage = { int numerator, int denominator ->
    denominator > 0 ? 100.0 * numerator / denominator : null
}

def outputDirectory = project.getPath().getParent().resolve('brain_cell_counter_results')
event('progress', [percent: 94, stage: 'Writing regional CSV'])
Files.createDirectories(outputDirectory)
String safeImageName = imageName.replaceAll('[^A-Za-z0-9._-]+', '_')
def outputFile = outputDirectory.resolve("${safeImageName}_regional_counts_v2.4.csv").toFile()
outputFile.withWriter('UTF-8') { writer ->
    writer.write('\uFEFF')
    String channel1Header = csvHeaderName(channel1Name)
    String channel2Header = csvHeaderName(channel2Name)
    String channel3Header = csvHeaderName(channel3Name)
    String doublePositiveHeader = "${channel1Header}+${channel2Header}"
    String triplePositiveHeader = "${channel3Header}+${channel1Header}+${channel2Header}"
    writer.writeLine("Image,Region_ID,Acronym,Region_Name,Major_Group,Functional_Subregion,Hierarchy_Level,Hemisphere,${channel3Header},${channel1Header}_Positive,${channel2Header}_Positive,${doublePositiveHeader},${triplePositiveHeader},Activated_${channel1Header}_Percent," +
            "Pixel_Size_X_um,Pixel_Size_Y_um,Region_Area_mm2,${channel3Header}_Density_per_mm2,${channel1Header}_Positive_Density_per_mm2,${channel2Header}_Positive_Density_per_mm2,${doublePositiveHeader}_Density_per_mm2,${triplePositiveHeader}_Density_per_mm2," +
            "${channel1Header}_Positive_Percent_of_${channel3Header},${channel2Header}_Positive_Percent_of_${channel3Header},${triplePositiveHeader}_Percent_of_${channel1Header},${triplePositiveHeader}_Percent_of_${channel2Header},Cellpose_Model,Functional_Subregion_Config")
    rows.values().sort { a, b ->
        (a.acronym <=> b.acronym) ?: (a.hemisphere <=> b.hemisphere)
    }.each { row ->
        String percent = formatMetric(percentage(row.triple as int, row.neun as int), 4)
        writer.writeLine([
                imageName, row.regionId, row.acronym, row.regionName, row.majorGroup, row.functionalSubregion, row.hierarchyLevel,
                row.hemisphere, row.dapi, row.neun, row.cfos, row.doublePositive, row.triple, percent,
                physicalCalibrationValid ? formatMetric(pixelWidthMicrons, 6) : '',
                physicalCalibrationValid ? formatMetric(pixelHeightMicrons, 6) : '',
                formatMetric(row.regionAreaMm2, 6),
                formatMetric(densityPerMm2(row.dapi as int, row.regionAreaMm2), 4),
                formatMetric(densityPerMm2(row.neun as int, row.regionAreaMm2), 4),
                formatMetric(densityPerMm2(row.cfos as int, row.regionAreaMm2), 4),
                formatMetric(densityPerMm2(row.doublePositive as int, row.regionAreaMm2), 4),
                formatMetric(densityPerMm2(row.triple as int, row.regionAreaMm2), 4),
                formatMetric(percentage(row.neun as int, row.dapi as int), 4),
                formatMetric(percentage(row.cfos as int, row.dapi as int), 4),
                formatMetric(percentage(row.triple as int, row.neun as int), 4),
                formatMetric(percentage(row.triple as int, row.cfos as int), 4),
                detectionModelLabel, functionalSubregionConfigLabel
        ].collect(csvEscape).join(','))
    }
}


// -----------------------------------------------------------------------------
// Atlas overlay PNG accompanying every regional CSV
// -----------------------------------------------------------------------------

event('progress', [percent: 96, stage: 'Rendering atlas overlay PNG'])
def server = imageData.getServer()
double atlasDownsample = Math.max(1.0, Math.max(server.width / 3200.0, server.height / 2200.0))
BufferedImage thumbnail = server.readRegion(atlasDownsample, 0, 0, server.width, server.height)
if (thumbnail == null)
    fail('QuPath could not render the atlas background thumbnail.')

int titleHeight = 54
BufferedImage atlasImage = new BufferedImage(
        thumbnail.width, thumbnail.height + titleHeight, BufferedImage.TYPE_INT_RGB)
// Keep the coordinate transform in script scope because the interactive cell
// overlay export reuses it after the graphics try/finally block has completed.
double atlasScaleX = thumbnail.width / (double)server.width
double atlasScaleY = thumbnail.height / (double)server.height
def graphics = atlasImage.createGraphics()
try {
    graphics.setColor(Color.BLACK)
    graphics.fillRect(0, 0, atlasImage.width, atlasImage.height)
    graphics.drawImage(thumbnail, 0, titleHeight, null)
    graphics.setRenderingHint(RenderingHints.KEY_ANTIALIASING, RenderingHints.VALUE_ANTIALIAS_ON)
    graphics.setColor(Color.WHITE)
    graphics.setFont(new Font('SansSerif', Font.BOLD, 22))
    String hemisphereTitle = hemisphereMode == 'left-right' ? 'Left and Right' : 'Do not split'
    graphics.drawString("${imageName} — Allen Atlas (${hemisphereTitle})", 18, 35)

    double atlasScale = Math.min(atlasScaleX, atlasScaleY)
    graphics.translate(0, titleHeight)
    graphics.scale(atlasScaleX, atlasScaleY)
    graphics.setStroke(new BasicStroke((float)(1.2 / atlasScale)))

    leafRegions.each { region ->
        def shape = RoiTools.getShape(region.getROI())
        int packedColor = region.getColor() ?: 0x4FA3FF
        int red = (packedColor >> 16) & 0xFF
        int green = (packedColor >> 8) & 0xFF
        int blue = packedColor & 0xFF
        graphics.setColor(new Color(red, green, blue, 72))
        graphics.fill(shape)
        graphics.setColor(new Color(255, 255, 255, 185))
        graphics.draw(shape)
    }

    // Place every leaf-region acronym in output-pixel coordinates. Labels are
    // moved to the nearest free position when centroids are crowded, with a
    // leader line back to the corresponding atlas region.
    graphics.setTransform(new AffineTransform())
    graphics.setFont(new Font('SansSerif', Font.BOLD, 12))
    def fontMetrics = graphics.getFontMetrics()
    def occupiedLabels = []
    def labelOffsets = [[0, 0]]
    (1..22).each { ring ->
        int radius = ring * 14
        labelOffsets.addAll([
                [0, -radius], [radius, -radius], [radius, 0], [radius, radius],
                [0, radius], [-radius, radius], [-radius, 0], [-radius, -radius]
        ])
    }

    leafRegions.sort { a, b -> b.getROI().getArea() <=> a.getROI().getArea() }.each { region ->
        def roi = region.getROI()
        String label = ontologyById[region.getName()?.toString()]?.acronym ?: region.getName()?.toString()
        if (label) {
            int anchorX = (int)Math.round(roi.getCentroidX() * atlasScaleX)
            int anchorY = titleHeight + (int)Math.round(roi.getCentroidY() * atlasScaleY)
            int labelWidth = fontMetrics.stringWidth(label) + 8
            int labelHeight = fontMetrics.height + 4
            Rectangle chosen = null
            labelOffsets.find { offset ->
                int labelX = anchorX - labelWidth / 2 + (offset[0] as int)
                int labelY = anchorY - labelHeight / 2 + (offset[1] as int)
                Rectangle candidate = new Rectangle(labelX, labelY, labelWidth, labelHeight)
                boolean inside = candidate.x >= 2 && candidate.y >= titleHeight + 2 &&
                        candidate.x + candidate.width < atlasImage.width - 2 &&
                        candidate.y + candidate.height < atlasImage.height - 2
                boolean overlaps = occupiedLabels.any { existing -> existing.intersects(candidate) }
                if (inside && !overlaps) {
                    chosen = candidate
                    return true
                }
                return false
            }
            if (chosen == null) {
                chosen = new Rectangle(
                        Math.max(2, Math.min(atlasImage.width - labelWidth - 2, anchorX - labelWidth / 2)),
                        Math.max(titleHeight + 2, Math.min(atlasImage.height - labelHeight - 2, anchorY - labelHeight / 2)),
                        labelWidth,
                        labelHeight)
            }
            occupiedLabels << chosen
            int boxX = (int)chosen.getX()
            int boxY = (int)chosen.getY()
            int boxWidth = (int)chosen.getWidth()
            int boxHeight = (int)chosen.getHeight()
            int labelCenterX = boxX + boxWidth / 2
            int labelCenterY = boxY + boxHeight / 2
            if (Math.abs(labelCenterX - anchorX) > 3 || Math.abs(labelCenterY - anchorY) > 3) {
                graphics.setStroke(new BasicStroke(1.0f))
                graphics.setColor(new Color(255, 255, 255, 150))
                graphics.drawLine(anchorX, anchorY, labelCenterX, labelCenterY)
            }
            graphics.setColor(new Color(0, 0, 0, 185))
            graphics.fillRoundRect(boxX, boxY, boxWidth, boxHeight, 6, 6)
            graphics.setColor(Color.WHITE)
            graphics.drawString(label, boxX + 4, boxY + 2 + fontMetrics.ascent)
        }
    }
} finally {
    graphics.dispose()
}

def atlasOutputFile = outputDirectory.resolve("${safeImageName}_atlas_overlay_v2.4.png").toFile()
if (!ImageIO.write(atlasImage, 'PNG', atlasOutputFile))
    fail('No PNG image writer is available for the atlas overlay.')
event('progress', [percent: 98, stage: 'Atlas overlay PNG complete'])

def summary = [
        version: '2.8.0',
        image: imageName,
        detectionSource: reused ? 'reused' : 'cellpose',
        cellposeModel: detectionModelLabel,
        counts: counts,
        channelNames: [channel1Name, channel2Name, channel3Name],
        hemisphereMode: hemisphereMode,
        hemisphereMidlineX: hemisphereMode == 'left-right' ? hemisphereMidlineX : null,
        leafRegionsAvailable: leafRegions.size(),
        regionsReported: rows.size(),
        regionsWithCells: rows.values().count { it.dapi > 0 || it.neun > 0 || it.cfos > 0 || it.triple > 0 },
        majorGroupsWithCells: rows.values().findAll {
            it.dapi > 0 || it.neun > 0 || it.cfos > 0 || it.triple > 0
        }.collect { it.majorGroup }.unique().sort(),
        functionalSubregionsWithCells: rows.values().findAll {
            it.functionalSubregion && (it.dapi > 0 || it.neun > 0 || it.cfos > 0 || it.triple > 0)
        }.collect { it.functionalSubregion }.unique().sort(),
        functionalSubregionSchema: functionalSubregionSchema,
        functionalSubregionConfigSha256: functionalSubregionConfigSha256,
        physicalCalibrationValid: physicalCalibrationValid,
        unassigned: unassigned,
        csv: outputFile.absolutePath,
        atlasImage: atlasOutputFile.absolutePath,
        interpretation: 'object-based co-expression; not pixel-level colocalization',
        thresholdStatus: 'temporary test values; biological validation required'
]
event('result', summary)
event('progress', [percent: 99, stage: 'Saving QuPath results'])
event('done', summary)
