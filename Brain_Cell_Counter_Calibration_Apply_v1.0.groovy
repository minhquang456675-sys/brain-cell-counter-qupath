// Brain Cell Counter - apply confirmed calibration to one QuPath entry
//
// This updates QuPath ImageServer metadata only. It does not resample or alter
// the source TIFF. The Python controller requires human confirmation and makes
// project-entry backups before launching this script with --save.

import qupath.lib.images.servers.ImageServerMetadata
import qupath.lib.io.GsonTools

def parameters = [pixelSizeMicrons: '']
args.each { argument ->
    int separator = argument.indexOf('=')
    if (separator > 0)
        parameters[argument.substring(0, separator)] = argument.substring(separator + 1)
}

def event = { String kind, Object payload ->
    println 'BCC_EVENT ' + GsonTools.getInstance().toJson([kind: kind, payload: payload])
}

double pixelSizeMicrons
try {
    pixelSizeMicrons = parameters.pixelSizeMicrons as double
} catch (Throwable ignored) {
    throw new IllegalArgumentException('A numeric pixelSizeMicrons value is required.')
}
if (!Double.isFinite(pixelSizeMicrons) || pixelSizeMicrons <= 0.0)
    throw new IllegalArgumentException('pixelSizeMicrons must be finite and greater than zero.')

def imageData = getCurrentImageData()
if (imageData == null)
    throw new IllegalStateException('QuPath did not open the project entry.')

def server = imageData.getServer()
def oldMetadata = server.getMetadata()
def newMetadata = new ImageServerMetadata.Builder(oldMetadata)
        .pixelSizeMicrons(pixelSizeMicrons, pixelSizeMicrons)
        .build()
imageData.updateServerMetadata(newMetadata)

def applied = server.getPixelCalibration()
def payload = [
        image             : getProjectEntry()?.getImageName() ?: oldMetadata.getName(),
        pixelWidthMicrons : applied.getPixelWidthMicrons(),
        pixelHeightMicrons: applied.getPixelHeightMicrons()
]
event('calibration_applied', payload)
event('done', payload)
