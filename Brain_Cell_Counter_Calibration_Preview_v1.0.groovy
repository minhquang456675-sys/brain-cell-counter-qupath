// Brain Cell Counter - non-saving calibration preview generator
//
// Opens either a QuPath project entry or a standalone image, renders a bounded
// preview through QuPath's ImageServer, and reports the source dimensions and
// existing pixel calibration. The source image is never modified.

import qupath.lib.io.GsonTools
import qupath.lib.regions.RegionRequest
import javax.imageio.ImageIO
import java.awt.Color
import java.awt.image.BufferedImage

def parameters = [outputPath: '', maxDimension: '1600']
args.each { argument ->
    int separator = argument.indexOf('=')
    if (separator > 0)
        parameters[argument.substring(0, separator)] = argument.substring(separator + 1)
}

def event = { String kind, Object payload ->
    println 'BCC_EVENT ' + GsonTools.getInstance().toJson([kind: kind, payload: payload])
}

def imageData = getCurrentImageData()
if (imageData == null)
    throw new IllegalStateException('QuPath did not open an image for calibration preview.')

File outputFile = new File(parameters.outputPath)
if (parameters.outputPath.trim().isEmpty())
    throw new IllegalArgumentException('Calibration preview outputPath is required.')
outputFile.parentFile?.mkdirs()

def server = imageData.getServer()
int sourceWidth = server.getWidth()
int sourceHeight = server.getHeight()
int maxDimension = Math.max(256, parameters.maxDimension as int)
double downsample = Math.max(1.0, Math.max(sourceWidth, sourceHeight) / (double) maxDimension)

def request = RegionRequest.createInstance(
        server.getPath(), downsample, 0, 0, sourceWidth, sourceHeight)
def serverPreview = server.readRegion(request)
def preview = new BufferedImage(serverPreview.getWidth(), serverPreview.getHeight(), BufferedImage.TYPE_INT_RGB)
def graphics = preview.createGraphics()
graphics.setColor(Color.BLACK)
graphics.fillRect(0, 0, preview.getWidth(), preview.getHeight())
graphics.drawImage(serverPreview, 0, 0, null)
graphics.dispose()
if (!ImageIO.write(preview, 'png', outputFile))
    throw new IllegalStateException('No PNG writer is available.')

def calibration = server.getPixelCalibration()
double pixelWidth = calibration.getPixelWidthMicrons()
double pixelHeight = calibration.getPixelHeightMicrons()

def payload = [
        image              : getProjectEntry()?.getImageName() ?: server.getMetadata().getName(),
        sourceWidth        : sourceWidth,
        sourceHeight       : sourceHeight,
        previewWidth       : preview.getWidth(),
        previewHeight      : preview.getHeight(),
        downsample         : downsample,
        previewPath        : outputFile.getAbsolutePath(),
        metadataPixelWidth : Double.isFinite(pixelWidth) ? pixelWidth : null,
        metadataPixelHeight: Double.isFinite(pixelHeight) ? pixelHeight : null
]
event('calibration_preview', payload)
event('done', payload)
