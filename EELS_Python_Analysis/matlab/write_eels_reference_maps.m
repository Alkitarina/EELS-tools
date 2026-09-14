function available = write_eels_reference_maps(outputFile, groupPath, info, yCount, xCount)
%WRITE_EELS_REFERENCE_MAPS Preserve retained total counts in the HDF5 UI schema.
% groupPath is empty for a single EELS file, or /eels/<variable> for an archive.
% Never reconstruct total counts from the processed spectrum image.

if isempty(groupPath)
    attributeTarget = '/';
else
    attributeTarget = groupPath;
end
h5writeatt(outputFile, attributeTarget, 'reference_maps_schema', 'eels_reference_maps_v1');
available = isstruct(info) && isfield(info, 'totint') && ~isempty(info.totint);
if ~available
    h5writeatt(outputFile, attributeTarget, 'total_counts_status', 'missing_in_source');
    return;
end
counts = info.totint;
if ~isnumeric(counts) || ~isreal(counts) || ~isequal(size(counts), [yCount, xCount])
    error('EELS:InvalidTotalCounts', ...
        'info.totint must be a real numeric [%d, %d] map matching this EELS object.', yCount, xCount);
end
if ~any(isfinite(counts(:)))
    error('EELS:InvalidTotalCounts', 'info.totint has no finite samples.');
end
countsPath = [groupPath '/reference_maps/total_counts'];
h5create(outputFile, countsPath, [yCount, xCount], 'Datatype', 'double');
h5write(outputFile, countsPath, double(counts));
% h5py sees MATLAB-written dimensions in reverse order, including square maps.
h5writeatt(outputFile, countsPath, 'axis_order', 'x_nm,y_nm');
h5writeatt(outputFile, countsPath, 'source_field', 'EELS.info.totint');
h5writeatt(outputFile, attributeTarget, 'total_counts_status', 'available');
end
