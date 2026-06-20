# Pinned ExifTool image for drone telemetry extraction (plans/drone-platform-plan.md).
#
# The DJI `dvtm_*` GPS track only decodes with ExifTool >= 13.x; the NAS's stock
# 12.86 reads identity tags but not the telemetry. This image pins a known-good
# ExifTool from CPAN (production = 13.55, the validated version). tools/import_drone.py
# --ssh runs it transiently on the NAS: `docker run --rm -v <media>:/data:ro exiftool:13x`.
# ExifTool reads the video bytes locally on the NAS; only telemetry text crosses the LAN.
#
# Build on the NAS (one-time, needs internet):
#   docker build -t exiftool:13x - < deploy/exiftool.Dockerfile

FROM perl:5.40-slim
RUN cpanm --notest Image::ExifTool \
 && exiftool -ver
ENTRYPOINT ["exiftool"]
