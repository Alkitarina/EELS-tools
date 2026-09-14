function outputFile = export_eels_to_hdf5(inputData, outputFile)
%EXPORT_EELS_TO_HDF5 Export an EELS object to a simple Python-readable HDF5 file.
%
% inputData can be either a MAT file path containing variable "data", or an
% EELS object already present in the MATLAB workspace, such as DeconvolutedData.
%
% The output contains the unchanged spectrum image and optional display map:
%   /intensity  [y, x, energy]
%   /x_nm
%   /y_nm
%   /energy_meV
%   /reference_maps/total_counts (when EELS.info.totint has matching geometry)
%
% Pixel-center coordinates start at 0 nm. The function does not modify the
% input MAT file or any toolbox source file.

if nargin < 1 || isempty(inputData)
    [fileName, folderName] = uigetfile('*.mat', 'Select an EELS MAT file');
    if isequal(fileName, 0)
        error('No input file selected.');
    end
    inputData = fullfile(folderName, fileName);
end

if nargin < 2 || isempty(outputFile)
    [fileName, folderName] = uiputfile('*.h5', 'Save Python-readable HDF5 file');
    if isequal(fileName, 0)
        error('No output file selected.');
    end
    outputFile = fullfile(folderName, fileName);
end

if isa(inputData, 'EELS')
    eels = inputData;
    sourceDescription = 'MATLAB workspace EELS object';
elseif ischar(inputData) || isstring(inputData)
    inputFile = char(inputData);
    loaded = load(inputFile, 'data');
    if ~isfield(loaded, 'data') || ~isa(loaded.data, 'EELS')
        error(['Expected variable "data" of class EELS. Add the 3D-EELS TOOLBOX ', ...
            'to the MATLAB path before calling this function.']);
    end
    eels = loaded.data;
    sourceDescription = inputFile;
else
    error('inputData must be an EELS object or a MAT file path.');
end
intensity = double(eels.data);
energy_meV = double(eels.ene(:));
yCount = size(intensity, 1);
xCount = size(intensity, 2);

x_nm = (0:xCount - 1)' * double(eels.info.xscale);
y_nm = (0:yCount - 1)' * double(eels.info.yscale);

if isfile(outputFile)
    delete(outputFile);
end

chunk = [min(yCount, 16), min(xCount, 16), min(size(intensity, 3), 128)];
h5create(outputFile, '/intensity', size(intensity), ...
    'Datatype', 'double', 'ChunkSize', chunk, 'Deflate', 4);
h5write(outputFile, '/intensity', intensity);

h5create(outputFile, '/x_nm', size(x_nm), 'Datatype', 'double');
h5write(outputFile, '/x_nm', x_nm);
h5create(outputFile, '/y_nm', size(y_nm), 'Datatype', 'double');
h5write(outputFile, '/y_nm', y_nm);
h5create(outputFile, '/energy_meV', size(energy_meV), 'Datatype', 'double');
h5write(outputFile, '/energy_meV', energy_meV);

h5writeatt(outputFile, '/', 'format', 'eels_python_hdf5_v1');
h5writeatt(outputFile, '/', 'source', sourceDescription);
h5writeatt(outputFile, '/intensity', 'axis_order', 'y_nm,x_nm,energy_meV');
h5writeatt(outputFile, '/intensity', 'coordinate_origin', ...
    'pixel centers; x_nm(1)=0; y_nm(1)=0');

write_eels_reference_maps(outputFile, '', eels.info, yCount, xCount);

if isfield(eels.info, 'tag')
    h5writeatt(outputFile, '/', 'processing_tag', char(string(eels.info.tag)));
end
if isfield(eels.info, 'zlpfwhm')
    h5writeatt(outputFile, '/', 'zlp_fwhm_meV', double(eels.info.zlpfwhm));
end

fprintf('Exported %d x %d x %d intensity cube to:\n%s\n', ...
    yCount, xCount, size(intensity, 3), outputFile);
fprintf('x: %.6g to %.6g nm, y: %.6g to %.6g nm, E: %.6g to %.6g meV\n', ...
    x_nm(1), x_nm(end), y_nm(1), y_nm(end), energy_meV(1), energy_meV(end));
end
