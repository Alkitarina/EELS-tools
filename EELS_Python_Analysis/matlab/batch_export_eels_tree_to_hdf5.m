function report = batch_export_eels_tree_to_hdf5(inputRoot, outputRoot, toolboxRoot)
%BATCH_EXPORT_EELS_TREE_TO_HDF5 Convert a MAT-file tree into Python-readable HDF5.
%
% Each source MAT file becomes one HDF5 file with the same relative folder
% and base file name. A MAT file with one EELS object writes the standard
% root datasets /intensity, /x_nm, /y_nm, and /energy_meV. Files containing
% multiple EELS objects store each object under /eels/<variable_name>/.
% Each EELS group also retains info.totint in reference_maps/total_counts.
% Other numeric MATLAB variables are retained under /matlab_variables/.
%
% This function does not edit the source MAT files or the 3D-EELS TOOLBOX.

if nargin < 3
    error('Provide inputRoot, outputRoot, and toolboxRoot.');
end

inputRoot = trim_trailing_separator(char(inputRoot));
outputRoot = trim_trailing_separator(char(outputRoot));
toolboxRoot = trim_trailing_separator(char(toolboxRoot));

if ~isfolder(inputRoot)
    error('Input root does not exist: %s', inputRoot);
end
if ~isfolder(toolboxRoot)
    error('3D-EELS TOOLBOX does not exist: %s', toolboxRoot);
end
if ~isfolder(outputRoot)
    mkdir(outputRoot);
end

% EELS objects must be resolvable before MATLAB loads them from MAT files.
addpath(genpath(toolboxRoot));

matFiles = dir(fullfile(inputRoot, '**', '*.mat'));
matFiles = matFiles(~[matFiles.isdir]);
sourcePaths = arrayfun(@(item) fullfile(item.folder, item.name), matFiles, ...
    'UniformOutput', false);
% Exclude the output subtree only; sibling input/output folders are valid.
isOutputFile = cellfun(@(file) is_within_path(file, outputRoot), sourcePaths);
matFiles = matFiles(~isOutputFile);
if isempty(matFiles)
    error('No source MAT files found under %s.', inputRoot);
end
records = cell(0, 8);

for index = 1:numel(matFiles)
    sourceFile = fullfile(matFiles(index).folder, matFiles(index).name);

    relativeFile = sourceFile(numel(inputRoot) + 2:end);
    [relativeFolder, baseName] = fileparts(relativeFile);
    destinationFolder = fullfile(outputRoot, relativeFolder);
    if ~isfolder(destinationFolder)
        mkdir(destinationFolder);
    end
    destinationFile = fullfile(destinationFolder, [baseName '.h5']);
    relativeOutput = fullfile(relativeFolder, [baseName '.h5']);

    fprintf('[%d/%d] Exporting %s\n', index, numel(matFiles), relativeFile);
    try
        summary = export_one_mat_file(sourceFile, destinationFile);
        records(end + 1, :) = {relativeFile, relativeOutput, 'ok', ...
            summary.eelsVariables, summary.numericVariables, summary.skippedVariables, ...
            summary.totalCountsVariables, summary.missingTotalCountsVariables}; %#ok<AGROW>
    catch exception
        warning('Failed to convert %s: %s', relativeFile, exception.message);
        records(end + 1, :) = {relativeFile, relativeOutput, 'failed', '', '', ...
            sprintf('%s: %s', exception.identifier, exception.message), '', ''}; %#ok<AGROW>
    end
end

report = cell2table(records, 'VariableNames', {
    'source_mat', 'output_h5', 'status', 'eels_variables', ...
    'numeric_variables', 'skipped_variables', 'total_counts_variables', ...
    'missing_total_counts_variables'});
writetable(report, fullfile(outputRoot, 'conversion_manifest.csv'), 'Encoding', 'UTF-8');

completed = sum(strcmp(report.status, 'ok'));
failed = height(report) - completed;
fprintf('Completed %d MAT to HDF5 conversions. Failed: %d.\n', completed, failed);
fprintf('Manifest: %s\n', fullfile(outputRoot, 'conversion_manifest.csv'));
end


function summary = export_one_mat_file(sourceFile, destinationFile)
temporaryFile = [destinationFile '.partial'];
if isfile(temporaryFile)
    delete(temporaryFile);
end

metadata = whos('-file', sourceFile);
eelsMetadata = metadata(strcmp({metadata.class}, 'EELS'));
singleEelsLayout = isscalar(eelsMetadata);
summary = struct('eelsVariables', '', 'numericVariables', '', 'skippedVariables', '', ...
    'totalCountsVariables', '', 'missingTotalCountsVariables', '');

try
    initialize_hdf5_file(temporaryFile, sourceFile, singleEelsLayout);
    eelsVariables = {};
    numericVariables = {};
    skippedVariables = {};
    totalCountsVariables = {};
    missingTotalCountsVariables = {};

    for variableIndex = 1:numel(metadata)
        variableName = metadata(variableIndex).name;
        variableClass = metadata(variableIndex).class;

        if strcmp(variableClass, 'EELS')
            loaded = load(sourceFile, variableName);
            eels = loaded.(variableName);
            if singleEelsLayout
                groupPath = '';
            else
                groupPath = ['/eels/' variableName];
            end
            hasCounts = write_eels_object(temporaryFile, groupPath, variableName, eels);
            eelsVariables{end + 1} = variableName; %#ok<AGROW>
            if hasCounts
                totalCountsVariables{end + 1} = variableName; %#ok<AGROW>
            else
                missingTotalCountsVariables{end + 1} = variableName; %#ok<AGROW>
            end
            clear loaded eels
        elseif is_numeric_matlab_class(variableClass)
            loaded = load(sourceFile, variableName);
            value = loaded.(variableName);
            write_numeric_variable(temporaryFile, ['/matlab_variables/' variableName], value);
            numericVariables{end + 1} = variableName; %#ok<AGROW>
            clear loaded value
        else
            skippedVariables{end + 1} = [variableName '(' variableClass ')']; %#ok<AGROW>
        end
    end

    h5writeatt(temporaryFile, '/', 'eels_variables', join_names(eelsVariables));
    h5writeatt(temporaryFile, '/', 'numeric_variables', join_names(numericVariables));
    h5writeatt(temporaryFile, '/', 'skipped_variables', join_names(skippedVariables));
    if singleEelsLayout
        h5writeatt(temporaryFile, '/', 'layout', 'single_eels_at_root');
    else
        h5writeatt(temporaryFile, '/', 'layout', 'multiple_eels_under_eels_group');
    end

    [wasMoved, message] = movefile(temporaryFile, destinationFile, 'f');
    if ~wasMoved
        error('Could not move temporary output to destination: %s', message);
    end
    summary.eelsVariables = join_names(eelsVariables);
    summary.numericVariables = join_names(numericVariables);
    summary.skippedVariables = join_names(skippedVariables);
    summary.totalCountsVariables = join_names(totalCountsVariables);
    summary.missingTotalCountsVariables = join_names(missingTotalCountsVariables);
catch exception
    if isfile(temporaryFile)
        delete(temporaryFile);
    end
    rethrow(exception);
end
end


function initialize_hdf5_file(outputFile, sourceFile, singleEelsLayout)
h5create(outputFile, '/metadata/export_version', [1 1], 'Datatype', 'uint8');
h5write(outputFile, '/metadata/export_version', uint8(2));
h5writeatt(outputFile, '/', 'format', 'eels_python_hdf5_v2');
h5writeatt(outputFile, '/', 'source_mat_file', sourceFile);
h5writeatt(outputFile, '/', 'single_eels_root_layout', uint8(singleEelsLayout));
end


function hasCounts = write_eels_object(outputFile, groupPath, variableName, eels)
intensity = double(eels.data);
energyMeV = double(eels.ene(:));

if ndims(intensity) ~= 3
    error('EELS variable %s does not contain a 3D intensity cube.', variableName);
end
if size(intensity, 3) ~= numel(energyMeV)
    error('EELS variable %s has inconsistent intensity and energy sizes.', variableName);
end

yCount = size(intensity, 1);
xCount = size(intensity, 2);
xNm = (0:xCount - 1)' * double(eels.info.xscale);
yNm = (0:yCount - 1)' * double(eels.info.yscale);

intensityPath = group_child_path(groupPath, 'intensity');
xPath = group_child_path(groupPath, 'x_nm');
yPath = group_child_path(groupPath, 'y_nm');
energyPath = group_child_path(groupPath, 'energy_meV');

write_dataset(outputFile, intensityPath, intensity, true);
write_dataset(outputFile, xPath, xNm, false);
write_dataset(outputFile, yPath, yNm, false);
write_dataset(outputFile, energyPath, energyMeV, false);

hasCounts = write_eels_reference_maps(outputFile, groupPath, eels.info, yCount, xCount);

attributeTarget = group_or_root(groupPath);
h5writeatt(outputFile, attributeTarget, 'source_variable', variableName);
h5writeatt(outputFile, intensityPath, 'axis_order', 'y_nm,x_nm,energy_meV');
h5writeatt(outputFile, intensityPath, 'coordinate_origin', ...
    'pixel centers; x_nm(1)=0; y_nm(1)=0');

if isstruct(eels.info) && isfield(eels.info, 'tag')
    h5writeatt(outputFile, attributeTarget, 'processing_tag', char(string(eels.info.tag)));
end
if isstruct(eels.info) && isfield(eels.info, 'zlpfwhm')
    h5writeatt(outputFile, attributeTarget, 'zlp_fwhm_meV', double(eels.info.zlpfwhm));
end
end


function write_numeric_variable(outputFile, variablePath, value)
if isempty(value)
    h5create(outputFile, variablePath, [1 1], 'Datatype', 'uint8');
    h5write(outputFile, variablePath, uint8(0));
    h5writeatt(outputFile, variablePath, 'is_empty_matlab_array', uint8(1));
    h5writeatt(outputFile, variablePath, 'matlab_class', class(value));
    return;
end

if islogical(value)
    write_dataset(outputFile, variablePath, uint8(value), true);
    h5writeatt(outputFile, variablePath, 'matlab_class', 'logical');
elseif isreal(value)
    write_dataset(outputFile, variablePath, value, true);
    h5writeatt(outputFile, variablePath, 'matlab_class', class(value));
else
    write_dataset(outputFile, [variablePath '/real'], real(value), true);
    write_dataset(outputFile, [variablePath '/imaginary'], imag(value), true);
    h5writeatt(outputFile, variablePath, 'matlab_class', class(value));
    h5writeatt(outputFile, variablePath, 'complex_encoding', ...
        'real_and_imaginary_children');
end
end


function write_dataset(outputFile, datasetPath, value, allowCompression)
dimensions = size(value);
dataType = class(value);

if allowCompression && numel(value) >= 4096 && all(dimensions > 0)
    chunkSize = min(dimensions, chunk_limits(numel(dimensions)));
    h5create(outputFile, datasetPath, dimensions, 'Datatype', dataType, ...
        'ChunkSize', chunkSize, 'Deflate', 4);
else
    h5create(outputFile, datasetPath, dimensions, 'Datatype', dataType);
end
h5write(outputFile, datasetPath, value);
end


function limits = chunk_limits(dimensionCount)
limits = 64 * ones(1, dimensionCount);
if dimensionCount >= 3
    limits(3) = 128;
end
end


function answer = is_numeric_matlab_class(className)
numericClasses = {'double', 'single', 'int8', 'uint8', 'int16', 'uint16', ...
    'int32', 'uint32', 'int64', 'uint64', 'logical'};
answer = ismember(className, numericClasses);
end


function pathOut = group_child_path(groupPath, childName)
if isempty(groupPath)
    pathOut = ['/' childName];
else
    pathOut = [groupPath '/' childName];
end
end


function pathOut = group_or_root(groupPath)
if isempty(groupPath)
    pathOut = '/';
else
    pathOut = groupPath;
end
end


function joined = join_names(names)
if isempty(names)
    joined = '';
else
    joined = strjoin(names, '|');
end
end


function answer = is_within_path(childPath, parentPath)
parentPrefix = [trim_trailing_separator(parentPath) filesep];
answer = startsWith(childPath, parentPrefix, 'IgnoreCase', true);
end


function pathOut = trim_trailing_separator(pathIn)
pathOut = char(pathIn);
while numel(pathOut) > 3 && (pathOut(end) == '\' || pathOut(end) == '/')
    pathOut(end) = [];
end
end
