import os
import re
import pydicom
from loguru import logger
from func_timeout import func_set_timeout, FunctionTimedOut
import SimpleITK as sitk
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext
from threading import Thread
from dataclasses import dataclass
from collections import Counter

# from tqdm.auto import tqdm

# from joblib import Parallel, delayed
# from multiprocessing import freeze_support

_version = "1.74"


@logger.catch
def get_dicom_file(root_path, timeout=2):
	@func_set_timeout(timeout)
	def get_dicom_file_inner(root_path):
		return_files = []
		for root, dirs, files in os.walk(root_path):
			if not dirs:
				for file in files:
					return_files.append(os.path.join(root, file))
		return return_files

	try:
		return get_dicom_file_inner(root_path)
	except FunctionTimedOut:
		logger.error(f"Timeout when reading {root_path}.")
		raise FunctionTimedOut(
			f"Timeout when reading {root_path}. maybe the path does not contain DICOM files or too many files. "
			f"If you are sure that the path contains DICOM files, you can increase the timeout limit."
		)


@logger.catch
def get_metadata(dicom_file, meta_keys=None):
	try:
		ds = pydicom.dcmread(dicom_file, force=True)
	except Exception as e:
		logger.warning(f"Error in reading {dicom_file}. Error: {e}, Will Skip.")
		# raise ValueError(f"Error in reading {dicom_file}. Error: {e}")
		return None

	d = {"file_path": dicom_file}

	for k in meta_keys:
		d[k] = ds.get(k, "[NA]")

	if "[NA]" == d["AccessionNumber"] or (
		d["AccessionNumber"] == "" and d["StudyID"] == ""
	):
		logger.warning(
			f"AccessionNumber and StudyID are [NA] in {dicom_file} !!!, will skip."
		)
		return None

	if "[NA]" != d["AcquisitionTime"] and d["AcquisitionTime"] != "":
		d["AcquisitionTime"] = str(round(float(d["AcquisitionTime"])))

	return d


def filter_in(x: dict):
	"""
	根据序列描述和厂商信息过滤序列
	x: dict
	"""
	if any(  # 通用过滤
		[
			i == x["SeriesDescription"].lower()
			for i in [
				"localizer",
				"3-pl loc",
				"3-pl loc ssfse",
				"3-pl ssfse loc",
				"processed images",
				"screen save",
				"default ps series",
				"survey",
			]
		]
	):
		return False

	if "Philips" in x["Manufacturer"] and (
		any(  # 有的序列描述中包含以下关键字
			[
				i in x["SeriesDescription"]
				for i in [
					"RECON",
				]
			]
		)
		or any(  # 有的序列描述中等于以下关键字
			[
				i == x["SeriesDescription"]
				for i in [
					"IN",
					"OP",
					"WATER",
					"ALL",
					"A1",
					"A2",
					"A60",
					"V60",
					"3min",
					"8min",
				]
			]
		)
	):
		return False
	if "GE" in x["Manufacturer"] and (
		any(  # 有的序列描述中包含以下关键字
			[
				i in x["SeriesDescription"]
				for i in [
					"ORIG",
					"MPR",
					"Refomate",
					"IDEAL IQ",
				]
			]
		)
		or any(  # 有的序列描述中等于以下关键字
			i == x["SeriesDescription"]
			for i in [
				"Ax LAVA-xv 5",
				"Ax LAVA-xv 10",
				"Ax LAVA-xv 15",
				"Water",
				"inphase",
				"outphase",
				"Processed Images",
				"LAVA 4 min",
				"LAVA 5 min",
				"LAVA 8 min",
				"InPhase: IDEAL IQ (20sec BH)",
				"WATER: IDEAL IQ (20sec BH)",
				"FAT: IDEAL IQ (20sec BH)",
				"OutPhase: IDEAL IQ (20sec BH)",

				"Ax fs DWI MULTI-b",
				" Ax fs DWI MULTI-b",
				"lava 4 min",
				"lava 5 min",
				"lava 8 min",
				"Ax LAVA-xv 5 120min",
				"Ax LAVA-xv 10 120min",
				"Ax LAVA-xv 15 120min",
				"Ax LAVA-xv 5  120min",
				"Ax LAVA-xv 10  120min",
				"Ax LAVA-xv 15  120min",
			]
		)
	):
		return False
	if "SIEMENS" in x["Manufacturer"] and (
		any(  # 有的序列描述中包含以下关键字
			[
				i in x["SeriesDescription"]
				for i in [
					"Map",
				]
			]
		)
		or any(  # 有的序列描述中等于以下关键字
			i == x["SeriesDescription"]
			for i in [
				"t1_vibe-twist_dixon_tra_p4_bh_pre_TTC=3.4s_F",
				"t1_vibe-twist_dixon_tra_p4_bh_art_5phases_TTC=7.7s_F",
				"t1_vibe-twist_dixon_tra_p4_bh_art_5phases_TTC=10.6s_F",
				"t1_vibe-twist_dixon_tra_p4_bh_art_5phases_TTC=13.4s_F",
				"t1_vibe-twist_dixon_tra_p4_bh_art_5phases_TTC=19.2s_F",
				"t1_vibe-twist_dixon_tra_p4_bh_art_5phases_TTC=16.3s_F",
				"t1_vibe-twist_dixon_tra_p4_50-60s_TTC=3.4s_F",
				"t1_vibe_dixon_cor_caipi6_bh_4-5min_F",
				"t1_vibe-twist_dixon_tra_p4_3-4min_TTC=3.4s_F",
				"t1_vibe-twist_dixon_tra_p4_8min_TTC=3.4s_F",
				"ep2d_diff_sms4_IVIM_TRACEW_DFC",
				"t1_1.8mm_dixon_tra_p4_120min_TTC=4.3s_F",
				"t1_vibe_dixon_cor_caipi6_120min_F",
				"t1_vibe_twist_dixon_tra_p4_120min_TTC=3.4s_F",
				"vibe_q-dixon_tra_p4_bh_WF",
				"vibe_q-dixon_tra_p4_bh_W",
				"vibe_q-dixon_tra_p4_bh_F",
			]
		)
	):
		return False
	return True


def sanitize_file_name(file_name):
	invalid_char_pattern = re.compile(r"[^a-zA-Z0-9._]")
	sanitized_file_name = invalid_char_pattern.sub("_", file_name)
	return sanitized_file_name


class DicomSeriesSplit:
	@logger.catch
	def __init__(
		self,
		timeout=4,
		n_jobs=4,
		backend=None,
		min_slices=24,
		skip_desc=None,
		filter_func=None,
		meta_keys=None,
		will_save_file_keys=None,
		will_save_folder_keys=None,
		will_save_root_path=None,
	):
		_meta_keys = [
			"PatientID",
			"AccessionNumber",
			"ProtocolName",
			"Manufacturer",
			"SeriesInstanceUID",
			"SliceLocation",
			"InstanceNumber",
			"SeriesNumber",
			"SeriesDescription",
			"AcquisitionTime",
			"AcquisitionNumber",
		]
		if meta_keys is None:
			meta_keys = _meta_keys
		elif set(meta_keys).issubset(_meta_keys):
			meta_keys = _meta_keys
		else:
			meta_keys = list(set(meta_keys).union(_meta_keys))

		if will_save_file_keys is None:
			will_save_file_keys = ["SeriesDescription"]

		if will_save_folder_keys is None:
			will_save_folder_keys = ["PatientID", "AccessionNumber"]

		if (
			will_save_root_path is None
			or not os.path.isdir(will_save_root_path)
			or not os.path.exists(will_save_root_path)
		):
			raise ValueError("will_save_root_path must be defined in advance")

		self.timeout = timeout
		self.n_jobs = n_jobs
		self.backend = backend
		self.min_slices = min_slices
		self.meta_keys = meta_keys
		self.will_save_file_keys = will_save_file_keys
		self.will_save_folder_keys = will_save_folder_keys
		self.will_save_root_path = will_save_root_path

		if skip_desc is None:
			self.skip_desc = {
				"localizer",
				"3-pl Loc",
				"3-Pl Loc SSFSE",
				"Processed Images",
				"Screen Save",
				"DEFAULT PS SERIES",
				"SURVEY",
			}
		else:
			self.skip_desc = skip_desc

		self.filter_func = filter_func

		logger.info(f"Create {self.__repr__()}")

	def __repr__(self):
		return (
			f"DicomSplitter(will_save_file_keys={self.will_save_file_keys} "
			f"will_save_folder_keys={self.will_save_folder_keys} "
			f"will_save_root_path={self.will_save_root_path}"
		)

	@logger.catch
	def __call__(self, _path):
		dicom_files = get_dicom_file(_path, timeout=self.timeout)
		logger.info(f"From {_path} Maybe Get {len(dicom_files)} DICOM files.")

		metadata_list = []
		for i, file in enumerate(dicom_files):
			metadata = get_metadata(file, self.meta_keys)
			metadata_list.append(metadata)
			logger.info(
				f"Read {i + 1}/{len(dicom_files)} DICOM files. Success."
			)

		metadata_list = list(filter(lambda x: x is not None, metadata_list))

		if len(metadata_list) == 0:
			raise ValueError(f"No valid metadata found in {dicom_files}")

		logger.info(
			f"Get {len(metadata_list)} DICOM files with valid metadata."
		)

		metadata_list = list(
			filter(
				lambda x: x["SliceLocation"] != "[NA]"
				and x["AcquisitionTime"] != "[NA]",
				metadata_list,
			)
		)

		if self.filter_func:
			metadata_list = self.filter_func(metadata_list)
		else:
			for key in self.will_save_file_keys:
				metadata_list = list(
					filter(
						lambda x: x[key] not in self.skip_desc, metadata_list
					)
				)
			metadata_list = list(filter(filter_in, metadata_list))

		metadata_list = sorted(
			metadata_list, key=lambda x: x["AcquisitionTime"]
		)

		series_dict = {}
		for metadata in metadata_list:
			series_instance_uid = metadata["SeriesInstanceUID"]
			if series_instance_uid not in series_dict:
				series_dict[series_instance_uid] = []
			series_dict[series_instance_uid].append(metadata)

		split_list = []
		index = 0
		will_save_folder_flag = None
		for key, value in series_dict.items():
			if len(value) <= self.min_slices:
				logger.info(
					f"Group {key} has {len(value)} slices, less than {self.min_slices}. Skip."
				)
				continue

			file_name_s = [
				sanitize_file_name(value[0][key])
				for key in self.will_save_file_keys
			]
			file_name = "-".join(
				[x if len(x) != 0 else "None" for x in file_name_s]
			)

			# if file_name == "BH_Ax_3D_DE_IN_OUT-3T_ABD_CARDIAC_5-154502":
			# 	pass
			
			value = sorted(
				value, key=lambda x: (x["SliceLocation"], x["InstanceNumber"])
			)

			aq_numbers = [x["AcquisitionNumber"] for x in value]
			aq_number_uniques = sorted(set(aq_numbers))

			slice_locations = [x["SliceLocation"] for x in value]
			slice_location_uniques = sorted(set(slice_locations))

			if len(aq_number_uniques) == 1:
				use_aq_number = False
			else:
				if len(value) % len(slice_location_uniques) == 0 and len(value) % len(aq_number_uniques) == 0:
					use_aq_number = False

				elif len(value) % len(aq_number_uniques) == 0:
					aq_numbers2 = list(aq_number_uniques) * (
						len(value) // len(aq_number_uniques)
					)
					if sorted(aq_numbers2) == sorted(aq_numbers):
						use_aq_number = True
					else:
						use_aq_number = False

				else:
					use_aq_number = False

			if use_aq_number:  # 多序列拆分 使用AcquisitionNumber
				logger.info(
					f"Use AcquisitionNumber Will Split {len(aq_number_uniques)} Series."
				)

				for aq_number in aq_number_uniques:
					aq_value = [
						x for x in value if x["AcquisitionNumber"] == aq_number
					]

					sub_files = [x["file_path"] for x in aq_value]

					# 1.3 sanitize_file_name will_save_folder
					patient_id = (
						value[0]["PatientID"]
						if value[0]["PatientID"] != ""
						else "NonePatientID"
					)
					accession_number = (
						value[0]["AccessionNumber"]
						if value[0]["AccessionNumber"] != ""
						else "NoneAccessionNumber"
					)
					study_id = (
						value[0]["StudyID"]
						if value[0]["StudyID"] != ""
						else "NoneStudyID"
					)

					patient_id = sanitize_file_name(patient_id)
					accession_number = sanitize_file_name(accession_number)
					study_id = sanitize_file_name(study_id)

					if (
						patient_id == "NonePatientID"
						and accession_number == "NoneAccessionNumber"
					):
						logger.warning(
							f"PatientID and AccessionNumber are [NA] in {sub_files[0]} !!!, will skip."
						)
						will_save_folder = (
							f"NonePatientID/NoneAccessionNumber/{study_id}"
						)
					else:
						will_save_folder = "/".join(
							[patient_id, accession_number]
						)

					index = (
						0
						if will_save_folder != will_save_folder_flag
						else index
					)
					will_save_folder_flag = will_save_folder
					series_data = SeriesData(
						index=index,
						files=sub_files,
						will_save_file=f"{file_name}-{aq_number}",
						will_save_folder=will_save_folder,
						will_save_root_path=self.will_save_root_path,
					)

					logger.info(f"Create {series_data} successfully.")

					split_list.append(series_data)
					index += 1
			else:  # 当AcquisitionNumber都相同时，尝试使用SliceLocation拆分
				location_value = [x["SliceLocation"] for x in value]

				location_counter = Counter(location_value)

				if not len(location_counter) == len(value):
					location_value = [
						item
						for item in location_value
						if location_counter[item] > 1
					]
					value = [
						x for x in value if x["SliceLocation"] in location_value
					]
					location_drop = [
						item
						for item in location_counter
						if location_counter[item] == 1
					]
					logger.info(
						f"Group {key} has {len(value)} slices, but only {len(location_value)} unique locations. Will Drop {len(location_drop)} locations."
					)

				location_uniques = sorted(set(location_value))

				# assert (
				#     len(value) % len(location_uniques) == 0
				# ), f"Group {key} has {len(value)} slices, but only {len(location_uniques)} unique locations."

				value_by_location = {}
				for location in location_uniques:
					value_by_location[location] = []
				for v in value:
					value_by_location[v["SliceLocation"]].append(v)

				split_num = len(value) // len(location_uniques)

				logger.info(f"Use SliceLocation Will Split {split_num} Series.")

				# if len(location_uniques) == 22:
				#     pass

				for i in range(split_num):
					sub_files = []
					for location in location_uniques:
						sub_files.append(
							value_by_location[location][i]["file_path"]
						)

					patient_id = (
						value[0]["PatientID"]
						if value[0]["PatientID"] != ""
						else "NonePatientID"
					)
					accession_number = (
						value[0]["AccessionNumber"]
						if value[0]["AccessionNumber"] != ""
						else "NoneAccessionNumber"
					)
					study_id = (
						value[0]["StudyID"]
						if value[0]["StudyID"] != ""
						else "NoneStudyID"
					)

					patient_id = sanitize_file_name(patient_id)
					accession_number = sanitize_file_name(accession_number)
					study_id = sanitize_file_name(study_id)

					if (
						patient_id == "NonePatientID"
						and accession_number == "NoneAccessionNumber"
					):
						logger.warning(
							f"PatientID and AccessionNumber are [NA] in {sub_files[0]} !!!, will skip."
						)
						will_save_folder = (
							f"NonePatientID/NoneAccessionNumber/{study_id}"
						)
					else:
						will_save_folder = "/".join(
							[patient_id, accession_number]
						)

					index = (
						0
						if will_save_folder != will_save_folder_flag
						else index
					)
					will_save_folder_flag = will_save_folder
					series_data = SeriesData(
						index=index,
						files=sub_files,
						will_save_file=f"{file_name}-{i}",
						will_save_folder=will_save_folder,
						will_save_root_path=self.will_save_root_path,
					)

					logger.info(f"Create {series_data} successfully.")

					split_list.append(series_data)
					index += 1

		self.split_list = split_list
		return split_list


@dataclass
class SeriesData:
	index: int
	files: list
	will_save_file: str
	will_save_folder: str
	will_save_root_path: str

	def __repr__(self):
		return f"SeriesData(index={self.index}, will_save_file={self.will_save_file}, files_length={len(self.files)})"

	@logger.catch
	def to_itk(self):
		reader = sitk.ImageSeriesReader()
		reader.SetFileNames(self.files)
		image = reader.Execute()

		return image

	@logger.catch
	def to_save_nifti(self):
		save_path = os.path.join(
			self.will_save_root_path, self.will_save_folder
		)
		if not os.path.exists(save_path):
			os.makedirs(save_path)

		save_file = os.path.join(
			save_path,
			f"{self.index:02d}-L{len(self.files):03d}-{self.will_save_file}.nii.gz",
		)
		if os.path.exists(save_file):
			logger.info(f"File {save_file} already exists. Skip.")
			return

		image = self.to_itk()

		sitk.WriteImage(image, save_file, True)
		logger.info(f"Save file {save_file} successfully.")


class DicomApp:
	def __init__(self, root):
		self.root = root
		self.root.title(f"DICOM Splitter v{_version}")
		self.root.geometry("800x630")

		self.label = tk.Label(root, text="Select DICOM Root Path:")
		self.label.grid(row=0, column=0, padx=10, pady=10, sticky=tk.W)
		self.path_entry = tk.Entry(root, width=70)
		self.path_entry.grid(row=0, column=1, padx=10, pady=10)

		self.save_label = tk.Label(root, text="Select NIFTI Save Path:")
		self.save_label.grid(row=1, column=0, padx=10, pady=10, sticky=tk.W)
		self.save_entry = tk.Entry(root, width=70)
		self.save_entry.grid(row=1, column=1, padx=10, pady=10)

		self.browse_button = tk.Button(
			root, text="Browse", command=self.browse_dicom
		)
		self.browse_button.grid(row=0, column=2, padx=10, pady=10)
		self.save_button = tk.Button(
			root, text="Browse", command=self.browse_save
		)
		self.save_button.grid(row=1, column=2, padx=10, pady=10)
		self.run_button = tk.Button(root, text="Run", command=self.run)
		self.run_button.grid(row=2, column=2, padx=10, pady=20)

		self.min_slices_label = tk.Label(
			root, text="Minimum Slices(排除的最小序列长度):"
		)
		self.min_slices_label.grid(
			row=2, column=0, padx=10, pady=10, sticky=tk.W
		)
		self.min_slices_entry = tk.Entry(root, width=20)
		self.min_slices_entry.grid(
			row=2, column=1, padx=10, pady=10, sticky=tk.W
		)
		self.min_slices_entry.insert(0, "10")

		self.timeout_label = tk.Label(
			root, text="Timeout (seconds, 防止程序卡死):"
		)
		self.timeout_label.grid(row=3, column=0, padx=10, pady=10, sticky=tk.W)
		self.timeout_entry = tk.Entry(root, width=20)
		self.timeout_entry.grid(row=3, column=1, padx=10, pady=10, sticky=tk.W)
		self.timeout_entry.insert(0, "2")

		save_file_format_example = (
			"Save File Format: SAVEPATH/PatientID/AccessionNumber/"
			"[Index]-L[length]-[SeriesDescription]-[ProtocolName]-[AcquisitionTime]-[I].nii.gz\n"
			"SAVEPATH: 保存文位置\t\tPatientID: 病人ID\t\tAccessionNumber: 检查号\n"
			"Index: 序列索引\t\t\tlength: 该序列长度\t\tSeriesDescription: 系列描述\n"
			"ProtocolName: 协议名称\t\tI: 多序列拆分标号\t\tAcquisitionTime: 序列采集时间\n"
		)
		self.save_file_format_label = tk.Label(
			root, text=save_file_format_example, justify=tk.LEFT, wraplength=700
		)
		self.save_file_format_label.grid(
			row=4, column=0, columnspan=3, padx=2, pady=2, sticky=tk.W
		)

		user_manual = (
			"This is a DICOM Splitter."
			"You can use it to split DICOM files into series and save them as NIfTI files.\n"
			"  1. Select a DICOM root path.\n"
			"  2. Select a save path.\n"
			"  3. Set the minimum number of slices for a series.\n"
			"  4. Set the timeout for reading DICOM files.\n"
			"  5. Click 'Run' to start the splitting process."
		)
		self.user_manual_label = tk.Label(
			root, text=user_manual, justify=tk.LEFT, wraplength=700
		)
		self.user_manual_label.grid(
			row=5, column=0, columnspan=3, padx=2, pady=2, sticky=tk.W
		)

		delveoper_info = "Name: Luoyang\nEmail: luoyang@stu.xidian.edu.cn"

		self.developer_label = tk.Label(
			root, text=delveoper_info, justify=tk.LEFT, wraplength=700
		)
		self.developer_label.grid(
			row=6, column=1, columnspan=2, padx=10, pady=10, sticky=tk.E
		)

		self.log_label = tk.Label(root, text="Log:")
		self.log_label.grid(row=6, column=0, padx=5, pady=5, sticky=tk.W)
		self.log_area = scrolledtext.ScrolledText(
			root, wrap=tk.WORD, width=100, height=10, state="disabled"
		)
		self.log_area.grid(
			row=7,
			column=0,
			columnspan=2,
			padx=1,
			pady=0,
			sticky=tk.W + tk.E + tk.N + tk.S,
		)

		# 配置 grid 行/列权重以使 log_area 可扩展
		root.grid_rowconfigure(6, weight=1)
		root.grid_columnconfigure(1, weight=1)

	def browse_dicom(self):
		directory = filedialog.askdirectory()
		if directory:
			self.path_entry.delete(0, tk.END)
			self.path_entry.insert(0, directory)

	def browse_save(self):
		directory = filedialog.askdirectory()
		if directory:
			self.save_entry.delete(0, tk.END)
			self.save_entry.insert(0, directory)

	def write_log(self, message):
		self.log_area.configure(state="normal")
		self.log_area.insert(tk.END, message + "\n")
		self.log_area.configure(state="disabled")
		self.log_area.yview(tk.END)

	@logger.catch
	def run(self):
		root_path = self.path_entry.get()
		save_path = self.save_entry.get()
		min_slices = self.min_slices_entry.get()
		timeout = self.timeout_entry.get()

		if not root_path:
			messagebox.showerror("Error", "Please select a DICOM root path")
			return
		if not save_path:
			messagebox.showerror("Error", "Please select a save path")
			return
		if not min_slices.isdigit() or int(min_slices) <= 0:
			messagebox.showerror(
				"Error", "Minimum slices must be a positive integer"
			)
			return
		if float(timeout) <= 0:
			messagebox.showerror("Error", "Timeout must be a positive number")
			return

		self.run_button.config(state=tk.DISABLED)

		thread = Thread(
			target=self.run_splitter,
			args=(root_path, save_path, int(min_slices), float(timeout)),
		)
		thread.start()

	@logger.catch
	def run_splitter(self, root_path, save_path, min_slices, timeout):
		root_path = root_path.replace(os.sep, "/")
		save_path = save_path.replace(os.sep, "/")

		meta_keys = [
			"PatientID",
			"StudyID",
			"AccessionNumber",
			"ProtocolName",
			"SeriesInstanceUID",
			"SliceLocation",
			"InstanceNumber",
			"SeriesNumber",
			"SeriesDescription",
			"AcquisitionTime",
		]

		split = DicomSeriesSplit(
			timeout=timeout,
			n_jobs=8,
			min_slices=min_slices,
			meta_keys=meta_keys,
			backend="spawn",
			# will_save_file_keys=["SeriesDescription"],
			will_save_file_keys=[
				"SeriesDescription",
				"ProtocolName",
				"AcquisitionTime",
			],
			will_save_folder_keys=["PatientID", "AccessionNumber"],
			will_save_root_path=save_path,
		)

		try:
			split_files = split(root_path)
			for split_file in split_files:
				split_file.to_save_nifti()
			messagebox.showinfo(
				"Success", "DICOM splitting and saving completed."
			)
		except Exception as e:
			messagebox.showerror("Error", f"An error occurred: {e}")
		finally:
			self.run_button.config(state=tk.NORMAL)


if __name__ == "__main__":
	# freeze_support()

	root = tk.Tk()
	app = DicomApp(root)

	class LogHandler:
		def __init__(self, app):
			self.app = app

		def write(self, message):
			if message.strip():  # ignore empty messages
				self.app.write_log(message.strip())

		def flush(self):
			pass

	log_save_path = "log"
	os.makedirs(log_save_path, exist_ok=True)
	logger.remove()
	logger.add(log_save_path + "/dicom_splitter_app.log", rotation="100 MB")
	logger.add(LogHandler(app))
	root.mainloop()

# pyinstaller -F -w --hiddenimport=pydicom.encoders.gdcm --hiddenimport=pydicom.encoders.pylibjpeg app.1.74.py -n DicomSplitter1.74.exe

# 同反相位 同时存在 AcquisitionNumber 和 SliceLocation 可分情况，使用 AcquisitionNumber 拆分会导致拆分错误，增加了 判断条件 此时使用 SliceLocation 拆分
