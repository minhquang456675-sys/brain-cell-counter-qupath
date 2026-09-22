// Brain Cell Counter - export one human-confirmed Fiji-calibrated OME-TIFF
//
// The source image is opened as a virtual Bio-Formats stack, calibrated in
// memory, and exported to a new file. The source image is never modified.

#@ File(label="Source image") sourceFile
#@ File(label="Calibrated OME-TIFF", style="save") outputFile
#@ File(label="Verification report", style="save") reportFile
#@ Double(label="Pixel size in micrometres") pixelSizeMicrons

import groovy.json.JsonOutput
import ij.measure.Calibration
import loci.plugins.BF
import loci.plugins.LociExporter
import loci.plugins.in.ImporterOptions

def emit = { String kind, Map payload ->
    println 'BCC_EVENT ' + JsonOutput.toJson([kind: kind, payload: payload])
}

if (sourceFile == null || !sourceFile.isFile())
    throw new IllegalArgumentException('An existing source image is required.')
if (outputFile == null)
    throw new IllegalArgumentException('An output OME-TIFF path is required.')
if (reportFile == null)
    throw new IllegalArgumentException('A verification report path is required.')
if (sourceFile.canonicalFile == outputFile.canonicalFile)
    throw new IllegalArgumentException('The calibrated output cannot overwrite the source image.')
if (pixelSizeMicrons == null || !Double.isFinite(pixelSizeMicrons) || pixelSizeMicrons <= 0.0)
    throw new IllegalArgumentException('pixelSizeMicrons must be finite and greater than zero.')
if (!outputFile.name.toLowerCase().endsWith('.ome.tif') &&
        !outputFile.name.toLowerCase().endsWith('.ome.tiff'))
    throw new IllegalArgumentException('The output filename must end with .ome.tif or .ome.tiff.')

outputFile.parentFile?.mkdirs()
reportFile.parentFile?.mkdirs()
if (outputFile.exists())
    throw new IllegalStateException('The output file already exists; existing calibrated images are never overwritten.')
if (reportFile.exists() && !reportFile.delete())
    throw new IllegalStateException('The previous verification report could not be removed.')

def openVirtual = { File path ->
    def options = new ImporterOptions()
    options.setId(path.absolutePath)
    options.setVirtual(true)
    options.setOpenAllSeries(false)
    options.setSeriesOn(0, true)
    def images = BF.openImagePlus(options)
    if (images == null || images.length != 1)
        throw new IllegalStateException("Expected one image series but Bio-Formats opened ${images?.length ?: 0}.")
    return images[0]
}

def source = openVirtual(sourceFile)
def expected = [
        width   : source.width,
        height  : source.height,
        channels: source.nChannels,
        slices  : source.nSlices,
        frames  : source.nFrames,
        bitDepth: source.bitDepth
]

try {
    Calibration calibration = source.getCalibration().copy()
    calibration.pixelWidth = pixelSizeMicrons
    calibration.pixelHeight = pixelSizeMicrons
    calibration.setUnit('um')
    source.setCalibration(calibration)

    def exporter = new LociExporter()
    String exporterArguments =
            "outfile=[${outputFile.absolutePath}] windowless=true compression=Uncompressed"
    exporter.setup(exporterArguments, source)
    exporter.run(source.getProcessor())
} finally {
    source.changes = false
    source.close()
}

if (!outputFile.isFile() || outputFile.length() <= 0)
    throw new IllegalStateException('Fiji did not create the calibrated OME-TIFF.')

def verified = openVirtual(outputFile)
try {
    def calibration = verified.getCalibration()
    def actual = [
            width            : verified.width,
            height           : verified.height,
            channels         : verified.nChannels,
            slices           : verified.nSlices,
            frames           : verified.nFrames,
            bitDepth         : verified.bitDepth,
            pixelWidthMicrons: calibration.pixelWidth,
            pixelHeightMicrons: calibration.pixelHeight,
            unit             : calibration.getUnit()
    ]
    ['width', 'height', 'channels', 'slices', 'frames', 'bitDepth'].each { key ->
        if (actual[key] != expected[key])
            throw new IllegalStateException("Verification failed for ${key}: expected ${expected[key]}, found ${actual[key]}.")
    }
    double tolerance = Math.max(1.0e-9, pixelSizeMicrons * 1.0e-6)
    if (Math.abs((actual.pixelWidthMicrons as double) - pixelSizeMicrons) > tolerance ||
            Math.abs((actual.pixelHeightMicrons as double) - pixelSizeMicrons) > tolerance)
        throw new IllegalStateException(
                "Verification failed for pixel size: expected ${pixelSizeMicrons}, " +
                "found ${actual.pixelWidthMicrons} x ${actual.pixelHeightMicrons}.")

    def payload = [
            sourcePath        : sourceFile.absolutePath,
            outputPath        : outputFile.absolutePath,
            outputBytes       : outputFile.length(),
            width             : actual.width,
            height            : actual.height,
            channels          : actual.channels,
            slices            : actual.slices,
            frames            : actual.frames,
            bitDepth          : actual.bitDepth,
            pixelWidthMicrons : actual.pixelWidthMicrons,
            pixelHeightMicrons: actual.pixelHeightMicrons,
            unit              : actual.unit,
            verified          : true
    ]
    reportFile.setText(JsonOutput.prettyPrint(JsonOutput.toJson(payload)), 'UTF-8')
    emit('fiji_calibration_exported', payload)
} finally {
    verified.changes = false
    verified.close()
}
