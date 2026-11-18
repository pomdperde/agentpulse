from __future__ import annotations

import re

from typing import NamedTuple

from agentpulse.core.models import Paper

# Google Scholar Metrics 式的一级学科门类（合并自 24 个 Scholar Metrics top-level subject areas）。
# 参考 https://scholar.google.com/citations?view_op=top_venues 分组。
FIELDS: tuple[str, ...] = (
    "计算机科学",          # Computer Science
    "工程学",              # Engineering + Chemical Engineering
    "材料科学",            # Materials Science（Scholar Metrics 独立一级）
    "数学",                # Mathematics
    "物理与天文学",        # Physics and Astronomy
    "化学",                # Chemistry
    "医学",                # Medicine + Dentistry + Nursing + Pharmacology + Health Professions + Veterinary
    "生物科学",            # Agricultural & Biological Sciences + Immunology & Microbiology + Neuroscience
    "经济与商业",          # Economics, Econometrics and Finance + Business, Management and Accounting
    "社会科学与心理学",    # Social Sciences + Psychology
    "地球与环境科学",      # Earth & Planetary Sciences + Environmental Science + Energy
    "人文与艺术",          # Arts & Humanities
)

# 每个学科门类下的常见检索主题；空字符串键为"全部门类"的通用主题。
TOPIC_PRESETS: dict[str, tuple[str, ...]] = {
    "": ("大模型推理", "多模态大模型", "扩散模型", "强化学习", "多智能体协作", "AI 安全"),
    "计算机科学": ("大模型推理", "多模态大模型", "扩散模型", "强化学习", "AI 安全", "多智能体协作"),
    "工程学": ("机器人学习", "数字孪生", "智能制造", "自动驾驶", "储能技术", "柔性电子"),
    "材料科学": ("超导材料", "二维材料", "高熵合金", "电池材料", "催化剂", "拓扑材料"),
    "数学": ("定理证明", "随机优化", "高维统计", "图论", "数值计算", "数学推理基准"),
    "物理与天文学": ("量子计算", "聚变能", "引力波", "暗物质", "粒子物理", "光子学"),
    "化学": ("分子生成", "催化剂设计", "电池材料", "有机合成", "计算化学", "绿色化学"),
    "医学": ("肿瘤免疫", "mRNA 疫苗", "基因编辑", "医学影像分析", "药物重定位", "精准医疗"),
    "生物科学": ("单细胞测序", "空间转录组", "CRISPR", "蛋白质结构预测", "微生物组学", "合成生物学"),
    "经济与商业": ("数字经济", "ESG 投资", "供应链韧性", "金融风险", "行为经济学", "生成式 AI 与就业"),
    "社会科学与心理学": ("平台治理", "社会网络分析", "教育公平", "算法偏见", "心理健康", "大模型社会影响"),
    "地球与环境科学": ("气候变化", "碳捕集", "极端天气", "海洋酸化", "遥感监测", "生物多样性"),
    "人文与艺术": ("数字人文", "AI 生成艺术", "文化遗产保护", "计算语言学", "媒介研究", "AI 伦理"),
}

# 各学科门类推荐的合法开放数据源（均为公开 API / 开放获取内容）
FIELD_SOURCES: dict[str, tuple[str, ...]] = {
    "": ("arxiv", "openalex", "crossref", "doaj", "zenodo"),
    "计算机科学": ("arxiv", "openreview", "openalex", "crossref", "hal"),
    "工程学": ("crossref", "openalex", "doaj", "zenodo"),
    "材料科学": ("openalex", "crossref", "doaj", "zenodo"),
    "数学": ("arxiv", "crossref", "doaj"),
    "物理与天文学": ("arxiv", "openalex", "crossref", "doaj"),
    "化学": ("crossref", "openalex", "doaj", "zenodo"),
    "医学": ("pubmed", "europepmc", "biorxiv", "doaj"),
    "生物科学": ("biorxiv", "europepmc", "pubmed", "doaj"),
    "经济与商业": ("openalex", "crossref", "hal", "doaj"),
    "社会科学与心理学": ("hal", "doaj", "openalex", "crossref"),
    "地球与环境科学": ("openalex", "crossref", "doaj", "zenodo"),
    "人文与艺术": ("hal", "doaj", "zenodo"),
}


class Subfield(NamedTuple):
    """二级学科：用于级联选择、细分过滤与热点主题推荐。"""

    name: str
    prefixes: tuple[str, ...] = ()
    hints: tuple[str, ...] = ()
    hotspots: tuple[tuple[str, tuple[str, ...]], ...] = ()


SUBFIELDS: dict[str, tuple[Subfield, ...]] = {
    # ========== 计算机科学 ==========
    "计算机科学": (
        Subfield("人工智能", ("cs.AI",), ("artificial intelligence",), (
            ("大模型推理", ("large language model reasoning", "chain of thought")),
            ("多模态大模型", ("multimodal large language model", "vision language model")),
            ("扩散模型", ("diffusion model", "text-to-image generation")),
            ("AI 安全", ("AI safety", "adversarial robustness")),
        )),
        Subfield("自然语言处理", ("cs.CL",), ("computation and language",), (
            ("检索增强生成", ("retrieval augmented generation", "RAG")),
            ("大模型对齐", ("LLM alignment", "RLHF")),
            ("长上下文建模", ("long context modeling", "long context LLM")),
            ("多语言模型", ("multilingual language model", "cross-lingual transfer")),
        )),
        Subfield("计算机视觉", ("cs.CV",), ("computer vision",), (
            ("视频生成", ("video generation", "text-to-video")),
            ("三维重建", ("3D reconstruction", "neural radiance field")),
            ("视觉语言模型", ("vision language model", "multimodal understanding")),
            ("图像编辑", ("image editing", "diffusion editing")),
        )),
        Subfield("机器学习", ("cs.LG",), ("machine learning",), (
            ("强化学习", ("reinforcement learning",)),
            ("图神经网络", ("graph neural network",)),
            ("元学习", ("meta-learning", "few-shot learning")),
            ("表征学习", ("representation learning", "self-supervised learning")),
        )),
        Subfield("软件工程", ("cs.SE",), ("software engineering",), (
            ("代码生成", ("code generation", "code LLM")),
            ("程序修复", ("automated program repair", "bug fixing")),
            ("软件测试自动化", ("automated software testing", "test generation")),
            ("代码智能体", ("coding agent", "software engineering agent")),
        )),
        Subfield("安全与隐私", ("cs.CR",), ("cryptography and security",), (
            ("对抗攻击", ("adversarial attack", "adversarial robustness")),
            ("大模型越狱", ("LLM jailbreak", "prompt injection")),
            ("联邦学习隐私", ("federated learning privacy", "differential privacy")),
            ("供应链安全", ("software supply chain security",)),
        )),
        Subfield("数据库与数据管理", ("cs.DB",), ("databases",), (
            ("向量数据库", ("vector database", "approximate nearest neighbor")),
            ("查询优化", ("query optimization",)),
            ("数据湖", ("data lake", "lakehouse")),
            ("数据治理", ("data governance", "data quality")),
        )),
        Subfield("分布式与网络", ("cs.DC", "cs.NI"), ("distributed computing", "networking"), (
            ("分布式训练", ("distributed training",)),
            ("云原生调度", ("cloud native scheduling", "kubernetes")),
            ("低时延网络", ("low-latency networking",)),
            ("算力优化", ("computing efficiency", "cluster scheduling")),
        )),
        Subfield("信息检索与推荐", ("cs.IR",), ("information retrieval", "recommendation"), (
            ("稠密检索", ("dense retrieval", "neural retrieval")),
            ("重排序", ("learning to rank", "reranking")),
            ("推荐系统", ("recommender system", "collaborative filtering")),
            ("图推荐", ("graph recommendation", "graph-based recommendation")),
        )),
        Subfield("人机交互", ("cs.HC",), ("human-computer interaction",), (
            ("交互设计", ("interaction design",)),
            ("可用性评估", ("usability evaluation",)),
            ("无障碍技术", ("accessibility technology",)),
            ("AR/VR 交互", ("augmented reality", "virtual reality interaction")),
        )),
        Subfield("系统架构", ("cs.OS", "cs.AR", "cs.PF"), ("operating systems", "architecture", "performance"), (
            ("操作系统", ("operating system",)),
            ("编译器优化", ("compiler optimization",)),
            ("异构计算", ("heterogeneous computing", "GPU cluster")),
            ("量子计算架构", ("quantum computer architecture",)),
        )),
    ),

    # ========== 工程学 ==========
    "工程学": (
        Subfield("机器人学", ("cs.RO",), ("robotics",), (
            ("机器人学习", ("robot learning", "robot manipulation")),
            ("具身智能", ("embodied AI", "embodied agent")),
            ("灵巧操作", ("dexterous manipulation",)),
            ("人机协作", ("human-robot collaboration",)),
        )),
        Subfield("控制与自动化", ("eess.SY",), ("systems and control",), (
            ("自动驾驶", ("autonomous driving",)),
            ("模型预测控制", ("model predictive control",)),
            ("多智能体控制", ("multi-agent control",)),
            ("工业质检", ("industrial inspection", "anomaly detection manufacturing")),
        )),
        Subfield("工业工程", (), ("industrial engineering", "manufacturing"), (
            ("智能制造", ("intelligent manufacturing", "smart factory")),
            ("数字孪生", ("digital twin",)),
            ("增材制造", ("additive manufacturing", "3D printing")),
            ("生产优化", ("production optimization", "supply chain engineering")),
        )),
        Subfield("能源工程", ("physics.app-ph",), ("energy engineering",), (
            ("储能技术", ("energy storage", "battery materials")),
            ("光伏电池", ("photovoltaic solar cell",)),
            ("燃料电池", ("fuel cell",)),
            ("热管理", ("thermal management",)),
        )),
        Subfield("航空航天", (), ("aerospace",), (
            ("航天器控制", ("spacecraft control",)),
            ("气动设计", ("aerodynamic design",)),
            ("卫星遥感", ("satellite remote sensing",)),
            ("低空经济", ("urban air mobility", "eVTOL")),
        )),
        Subfield("生物医学工程", ("eess.IV",), ("biomedical engineering",), (
            ("医学影像设备", ("medical imaging devices",)),
            ("可穿戴传感", ("wearable sensors",)),
            ("神经接口", ("neural interfaces",)),
            ("生物力学", ("biomechanics",)),
        )),
        Subfield("化学工程", (), ("chemical engineering",), (
            ("催化剂工程", ("catalyst engineering",)),
            ("过程优化", ("process optimization",)),
            ("绿色化工", ("green chemical process",)),
            ("分离技术", ("separation technology",)),
        )),
        Subfield("土木与交通", (), ("civil engineering", "transportation"), (
            ("智能交通", ("intelligent transportation system",)),
            ("结构健康监测", ("structural health monitoring",)),
            ("建筑信息模型", ("building information modeling", "BIM")),
            ("地下工程", ("underground engineering", "tunneling")),
        )),
        Subfield("电子与电气工程", ("eess.SP", "eess.CS", "eess.IV"), ("signal processing", "electronics"), (
            ("柔性电子", ("flexible electronics",)),
            ("电路设计", ("circuit design", "VLSI")),
            ("信号处理", ("signal processing", "compressed sensing")),
            ("传感器", ("sensor", "IoT sensing")),
        )),
    ),

    # ========== 材料科学 ==========
    "材料科学": (
        Subfield("超导与量子材料", ("cond-mat.supr-con",), ("superconductivity", "quantum materials"), (
            ("超导材料", ("superconductivity", "superconducting materials")),
            ("拓扑物态", ("topological states of matter",)),
            ("量子模拟", ("quantum simulation",)),
            ("莫尔超晶格", ("moiré superlattice",)),
        )),
        Subfield("二维与低维材料", ("cond-mat.mes-hall",), ("two-dimensional materials", "low-dimensional"), (
            ("二维材料", ("two-dimensional materials", "graphene")),
            ("纳米材料", ("nanomaterial", "nanoparticle")),
            ("量子点", ("quantum dot",)),
            ("范德华异质结", ("van der Waals heterostructure",)),
        )),
        Subfield("高熵与多主元合金", ("cond-mat.mtrl-sci",), ("high-entropy alloy",), (
            ("高熵合金", ("high-entropy alloy", "multi-principal element alloy")),
            ("金属间化合物", ("intermetallic compound",)),
            ("结构材料", ("structural material",)),
            ("材料基因组", ("materials genome", "materials discovery")),
        )),
        Subfield("能源材料", (), ("energy materials",), (
            ("电池材料", ("battery materials", "electrode materials")),
            ("固态电解质", ("solid-state electrolyte",)),
            ("光伏材料", ("photovoltaic material", "perovskite")),
            ("热电器件", ("thermoelectric material",)),
        )),
        Subfield("计算材料学", ("cond-mat",), ("computational materials science",), (
            ("第一性原理计算", ("first-principles calculation", "density functional theory")),
            ("分子动力学", ("molecular dynamics simulation",)),
            ("高通量筛选", ("high-throughput screening",)),
            ("机器学习加速", ("machine learning for materials",)),
        )),
        Subfield("智能材料与超材料", (), ("smart materials", "metamaterials"), (
            ("智能材料", ("smart materials", "self-healing material")),
            ("超材料", ("metamaterial", "metasurface")),
            ("柔性电子材料", ("flexible electronic material",)),
            ("形状记忆合金", ("shape memory alloy",)),
        )),
    ),

    # ========== 数学 ==========
    "数学": (
        Subfield("基础数学", ("math.LO", "math.CO", "math.GR", "math.AG", "math.RT"), ("logic", "combinatorics", "algebra"), (
            ("定理证明", ("theorem proving", "automated theorem prover")),
            ("形式化验证", ("formal verification", "proof assistant")),
            ("组合数学", ("combinatorics",)),
            ("代数几何", ("algebraic geometry",)),
        )),
        Subfield("应用数学", ("math.NA", "math.OC", "math.AP"), ("numerical analysis", "optimization", "PDE"), (
            ("随机优化", ("stochastic optimization",)),
            ("数值计算", ("numerical analysis", "numerical methods")),
            ("最优控制", ("optimal control",)),
            ("偏微分方程", ("partial differential equations",)),
        )),
        Subfield("统计学", ("stat.ME", "stat.ML", "stat.TH", "stat.AP"), ("statistics",), (
            ("高维统计", ("high-dimensional statistics",)),
            ("因果推断", ("causal inference",)),
            ("贝叶斯方法", ("Bayesian methods",)),
            ("分布外泛化", ("out-of-distribution generalization",)),
        )),
        Subfield("概率论与随机过程", ("math.PR",), ("probability", "stochastic"), (
            ("随机过程", ("stochastic processes",)),
            ("极限理论", ("limit theorems",)),
            ("随机矩阵", ("random matrices",)),
            ("大偏差理论", ("large deviations",)),
        )),
        Subfield("计算数学", ("math.NA", "cs.CC"), ("computational mathematics",), (
            ("数值线性代数", ("numerical linear algebra",)),
            ("有限元方法", ("finite element method",)),
            ("谱方法", ("spectral methods",)),
            ("计算复杂性", ("computational complexity", "P vs NP")),
        )),
        Subfield("拓扑与几何", ("math.GT", "math.DG", "math.AT"), ("topology", "geometry"), (
            ("代数拓扑", ("algebraic topology",)),
            ("微分几何", ("differential geometry",)),
            ("几何分析", ("geometric analysis",)),
            ("拓扑数据分析", ("topological data analysis", "persistent homology")),
        )),
        Subfield("图论与组合优化", ("math.CO", "cs.DM"), ("graph theory", "combinatorial optimization"), (
            ("图论", ("graph theory",)),
            ("组合优化", ("combinatorial optimization",)),
            ("随机图", ("random graph",)),
            ("网络科学", ("network science", "graph neural network")),
        )),
    ),

    # ========== 物理与天文学 ==========
    "物理与天文学": (
        Subfield("量子科学", ("quant-ph",), ("quantum",), (
            ("量子计算", ("quantum computing",)),
            ("量子纠错", ("quantum error correction",)),
            ("量子模拟", ("quantum simulation",)),
            ("量子网络", ("quantum network", "quantum communication")),
        )),
        Subfield("粒子物理", ("hep-ph", "hep-ex", "hep-th"), ("particle physics",), (
            ("对撞机物理", ("collider physics",)),
            ("中微子物理", ("neutrino physics",)),
            ("暗物质探测", ("dark matter detection",)),
            ("希格斯物理", ("Higgs boson",)),
        )),
        Subfield("天体与宇宙学", ("astro-ph", "gr-qc", "physics.HE"), ("astrophysics", "cosmology"), (
            ("引力波", ("gravitational waves",)),
            ("系外行星", ("exoplanets",)),
            ("暗物质与暗能量", ("dark matter", "dark energy")),
            ("宇宙学模拟", ("cosmological simulation",)),
        )),
        Subfield("光学与光子学", ("physics.optics", "physics.phot-ph"), ("optics", "photonics"), (
            ("光子学", ("photonics",)),
            ("超快激光", ("ultrafast laser",)),
            ("量子光学", ("quantum optics",)),
            ("光学计算", ("optical computing",)),
        )),
        Subfield("等离子体物理与聚变", ("physics.plasm-ph",), ("plasma physics", "fusion"), (
            ("聚变能", ("fusion energy", "tokamak")),
            ("等离子体约束", ("plasma confinement",)),
            ("惯性约束", ("inertial confinement fusion",)),
            ("空间等离子体", ("space plasma",)),
        )),
        Subfield("凝聚态物理", ("cond-mat",), ("condensed matter",), (
            ("超导材料", ("superconductivity", "superconducting materials")),
            ("拓扑物态", ("topological states of matter",)),
            ("二维材料", ("two-dimensional materials",)),
            ("莫尔超晶格", ("moiré superlattice",)),
        )),
        Subfield("生物物理", ("physics.bio-ph", "q-bio.BM"), ("biophysics",), (
            ("单分子物理", ("single-molecule biophysics",)),
            ("生物分子机器", ("biomolecular machine",)),
            ("细胞力学", ("cellular mechanics",)),
            ("计算生物物理", ("computational biophysics",)),
        )),
    ),

    # ========== 化学 ==========
    "化学": (
        Subfield("计算化学", ("physics.comp-ph",), ("computational chemistry", "quantum chemistry"), (
            ("分子生成", ("molecule generation", "de novo molecular design")),
            ("密度泛函理论", ("density functional theory", "DFT")),
            ("分子动力学", ("molecular dynamics simulation",)),
            ("量子化学", ("quantum chemistry", "ab initio")),
        )),
        Subfield("有机化学", (), ("organic chemistry",), (
            ("有机合成", ("organic synthesis",)),
            ("不对称催化", ("asymmetric catalysis", "chiral synthesis")),
            ("药物中间体", ("pharmaceutical intermediate",)),
            ("绿色有机合成", ("green organic synthesis",)),
        )),
        Subfield("无机化学", (), ("inorganic chemistry",), (
            ("配合物化学", ("coordination chemistry",)),
            ("金属有机框架", ("metal-organic framework", "MOF")),
            ("固态化学", ("solid-state chemistry",)),
            ("主族元素化学", ("main group chemistry",)),
        )),
        Subfield("物理化学", (), ("physical chemistry",), (
            ("化学反应动力学", ("chemical reaction dynamics",)),
            ("催化反应", ("catalysis", "heterogeneous catalysis")),
            ("电化学", ("electrochemistry",)),
            ("界面化学", ("surface chemistry", "interface science")),
        )),
        Subfield("生物化学", ("q-bio.BM",), ("biochemistry",), (
            ("蛋白质化学", ("protein chemistry", "protein design")),
            ("酶催化", ("enzyme catalysis", "enzymology")),
            ("代谢工程", ("metabolic engineering",)),
            ("核酸化学", ("nucleic acid chemistry",)),
        )),
        Subfield("绿色与环境化学", (), ("green chemistry", "environmental chemistry"), (
            ("绿色化学", ("green chemistry", "sustainable chemistry")),
            ("催化剂设计", ("catalyst design", "machine learning catalyst")),
            ("废物处理", ("waste treatment", "remediation chemistry")),
            ("碳化学", ("carbon chemistry", "CO2 utilization")),
        )),
    ),

    # ========== 医学 ==========
    "医学": (
        Subfield("肿瘤学", ("q-bio.CB", "q-bio.TO"), ("cancer", "oncology"), (
            ("肿瘤免疫", ("tumor immunology", "cancer immunotherapy")),
            ("癌症早筛", ("early cancer detection",)),
            ("靶向治疗", ("targeted therapy",)),
            ("空间转录组", ("spatial transcriptomics",)),
        )),
        Subfield("神经科学", ("q-bio.NC",), ("neuroscience",), (
            ("阿尔茨海默病", ("Alzheimer's disease",)),
            ("脑机接口", ("brain-computer interface",)),
            ("神经解码", ("neural decoding",)),
            ("类脑计算", ("neuromorphic computing",)),
        )),
        Subfield("临床医学", (), ("clinical medicine",), (
            ("医学影像分析", ("medical imaging analysis",)),
            ("临床决策支持", ("clinical decision support",)),
            ("电子病历", ("electronic health records",)),
            ("精准医疗", ("precision medicine", "personalized medicine")),
        )),
        Subfield("药物研发", ("q-bio.BM",), ("drug discovery", "pharmacology"), (
            ("药物发现", ("drug discovery",)),
            ("分子生成", ("molecule generation",)),
            ("蛋白结构预测", ("protein structure prediction",)),
            ("药物重定位", ("drug repurposing",)),
        )),
        Subfield("公共卫生", (), ("public health", "epidemiology"), (
            ("流行病学", ("epidemiology",)),
            ("疫苗开发", ("vaccine development", "mRNA vaccine")),
            ("健康公平", ("health equity",)),
            ("疾病监测", ("disease surveillance",)),
        )),
        Subfield("免疫学", ("q-bio.IB",), ("immunology",), (
            ("免疫治疗", ("immunotherapy", "CAR-T")),
            ("免疫检查点", ("immune checkpoint inhibitor",)),
            ("自身免疫病", ("autoimmune disease",)),
            ("疫苗平台", ("vaccine platform technology",)),
        )),
        Subfield("病理学与遗传学", ("q-bio.GN",), ("pathology", "medical genetics"), (
            ("基因编辑", ("CRISPR gene editing",)),
            ("遗传咨询", ("genetic counseling",)),
            ("罕见病", ("rare disease", "orphan disease")),
            ("液体活检", ("liquid biopsy", "circulating tumor DNA")),
        )),
        Subfield("牙医学与口腔科学", (), ("dentistry", "oral science"), (
            ("口腔材料", ("dental materials",)),
            ("骨组织再生", ("bone tissue regeneration",)),
            ("种植修复", ("dental implant", "prosthodontics")),
            ("口腔AI", ("AI in dentistry", "dental image analysis")),
        )),
    ),

    # ========== 生物科学 ==========
    "生物科学": (
        Subfield("基因组学", ("q-bio.GN", "q-bio.QM"), ("genomics", "genetics"), (
            ("基因编辑", ("CRISPR gene editing",)),
            ("单细胞测序", ("single-cell sequencing",)),
            ("基因组注释", ("genome annotation",)),
            ("多组学整合", ("multi-omics integration",)),
        )),
        Subfield("蛋白质与结构生物学", ("q-bio.BM",), ("protein", "structural biology"), (
            ("蛋白结构预测", ("protein structure prediction",)),
            ("蛋白质设计", ("protein design", "de novo protein")),
            ("结构生物学", ("structural biology", "cryo-EM")),
            ("分子动力学", ("molecular dynamics",)),
        )),
        Subfield("合成生物学", ("q-bio.SC",), ("synthetic biology",), (
            ("基因线路设计", ("genetic circuit design",)),
            ("代谢工程", ("metabolic engineering",)),
            ("底盘生物", ("chassis organism",)),
            ("生物计算", ("biological computation", "DNA computing")),
        )),
        Subfield("微生物学", ("q-bio.MN",), ("microbiology",), (
            ("微生物组学", ("microbiome",)),
            ("细菌耐药", ("antibiotic resistance",)),
            ("噬菌体疗法", ("phage therapy",)),
            ("发酵工程", ("fermentation engineering", "industrial microbiology")),
        )),
        Subfield("细胞生物学", ("q-bio.CB",), ("cell biology",), (
            ("细胞信号转导", ("cell signaling", "signal transduction")),
            ("细胞周期", ("cell cycle",)),
            ("干细胞", ("stem cell", "induced pluripotent stem cell")),
            ("细胞成像", ("cell imaging", "quantitative cell biology")),
        )),
        Subfield("生态学与进化", ("q-bio.PE",), ("ecology", "evolution"), (
            ("生物多样性", ("biodiversity",)),
            ("种群动态", ("population dynamics",)),
            ("系统发育", ("phylogenetics", "molecular evolution")),
            ("宏基因组学", ("metagenomics", "environmental DNA")),
        )),
    ),

    # ========== 经济与商业 ==========
    "经济与商业": (
        Subfield("经济学", ("econ",), ("economics",), (
            ("数字经济", ("digital economy",)),
            ("行为经济学", ("behavioral economics",)),
            ("因果推断", ("causal inference",)),
            ("劳动市场", ("labor market",)),
        )),
        Subfield("金融学", ("q-fin",), ("finance",), (
            ("金融风险", ("financial risk",)),
            ("ESG 投资", ("ESG investing",)),
            ("资产定价", ("asset pricing",)),
            ("高频交易", ("high-frequency trading",)),
        )),
        Subfield("管理学", ("econ.GN",), ("management", "organization"), (
            ("供应链韧性", ("supply chain resilience",)),
            ("平台治理", ("platform governance",)),
            ("组织行为", ("organizational behavior",)),
            ("运营优化", ("operations optimization",)),
        )),
        Subfield("营销与消费者行为", (), ("marketing", "consumer behavior"), (
            ("推荐系统", ("recommender system",)),
            ("广告投放", ("computational advertising",)),
            ("消费者洞察", ("consumer insight", "user research")),
            ("社交媒体营销", ("social media marketing",)),
        )),
        Subfield("会计学与公司治理", ("econ.GN",), ("accounting", "corporate governance"), (
            ("财务报告", ("financial reporting",)),
            ("审计", ("auditing",)),
            ("公司治理", ("corporate governance",)),
            ("ESG 披露", ("ESG disclosure",)),
        )),
        Subfield("创新与创业", ("econ",), ("innovation", "entrepreneurship"), (
            ("技术创新", ("technological innovation",)),
            ("创业融资", ("entrepreneurial finance", "venture capital")),
            ("产业政策", ("industrial policy",)),
            ("专利分析", ("patent analysis",)),
        )),
        Subfield("计量经济学", ("econ.EM",), ("econometrics",), (
            ("因果推断", ("causal inference", "difference-in-differences")),
            ("面板数据", ("panel data",)),
            ("工具变量", ("instrumental variable",)),
            ("机器学习计量", ("machine learning econometrics",)),
        )),
    ),

    # ========== 社会科学与心理学 ==========
    "社会科学与心理学": (
        Subfield("社会学", (), ("sociology",), (
            ("社会网络分析", ("social network analysis",)),
            ("算法偏见", ("algorithmic bias",)),
            ("人口老龄化", ("population aging",)),
            ("社会分层", ("social stratification",)),
        )),
        Subfield("政治学", (), ("political science",), (
            ("政策文本分析", ("policy text analysis",)),
            ("选举预测", ("election forecasting",)),
            ("数字治理", ("digital governance",)),
            ("国际关系量化", ("quantitative international relations",)),
        )),
        Subfield("教育学", (), ("education",), (
            ("教育公平", ("education equity",)),
            ("智能私教", ("intelligent tutoring system",)),
            ("学习分析", ("learning analytics",)),
            ("在线教育", ("online learning",)),
        )),
        Subfield("传播学", (), ("communication",), (
            ("媒介研究", ("media studies",)),
            ("虚假信息", ("misinformation", "disinformation")),
            ("舆情分析", ("public opinion analysis",)),
            ("推荐算法影响", ("recommendation algorithm", "filter bubble")),
        )),
        Subfield("心理学", (), ("psychology",), (
            ("认知心理学", ("cognitive psychology",)),
            ("社会心理学", ("social psychology",)),
            ("心理测量", ("psychometrics",)),
            ("心理健康", ("mental health", "computational psychiatry")),
        )),
        Subfield("法学", (), ("law", "legal studies"), (
            ("法律人工智能", ("AI in law", "legal AI")),
            ("隐私与数据保护", ("privacy law", "data protection")),
            ("知识产权", ("intellectual property law",)),
            ("算法治理", ("algorithmic governance")),
        )),
        Subfield("人类学", (), ("anthropology",), (
            ("数字人类学", ("digital anthropology",)),
            ("文化计算", ("computational culture",)),
            ("社会模拟", ("social simulation", "agent-based model")),
            ("人类行为建模", ("human behavior modeling",)),
        )),
    ),

    # ========== 地球与环境科学 ==========
    "地球与环境科学": (
        Subfield("气候科学", ("physics.ao-ph",), ("climate",), (
            ("气候变化", ("climate change",)),
            ("极端天气", ("extreme weather",)),
            ("气候模式", ("climate modeling",)),
            ("碳循环", ("carbon cycle",)),
        )),
        Subfield("环境工程", ("physics.soc-ph",), ("environmental",), (
            ("碳捕集", ("carbon capture",)),
            ("水处理", ("water treatment",)),
            ("污染物监测", ("pollutant monitoring",)),
            ("环境修复", ("environmental remediation",)),
        )),
        Subfield("地球科学", ("physics.geo-ph",), ("geophysics", "geology"), (
            ("地震预测", ("earthquake prediction",)),
            ("遥感监测", ("remote sensing",)),
            ("地质建模", ("geological modeling",)),
            ("地表形变", ("surface deformation",)),
        )),
        Subfield("生态学", ("q-bio.PE",), ("ecology",), (
            ("生物多样性", ("biodiversity",)),
            ("生态系统建模", ("ecosystem modeling",)),
            ("物种保护", ("species conservation",)),
            ("入侵物种", ("invasive species",)),
        )),
        Subfield("海洋科学", ("physics.ao-ph",), ("oceanography",), (
            ("海洋酸化", ("ocean acidification",)),
            ("海洋环流", ("ocean circulation",)),
            ("珊瑚礁", ("coral reefs",)),
            ("海洋遥感", ("ocean remote sensing",)),
        )),
        Subfield("能源与碳汇", (), ("energy science", "carbon sink"), (
            ("碳中和", ("carbon neutrality",)),
            ("可再生能源", ("renewable energy",)),
            ("碳汇", ("carbon sink", "forest carbon")),
            ("能源系统建模", ("energy system modeling",)),
        )),
        Subfield("大气科学", ("physics.ao-ph",), ("atmospheric science",), (
            ("大气污染", ("air pollution",)),
            ("空气质量", ("air quality", "PM2.5")),
            ("大气化学", ("atmospheric chemistry",)),
            ("天气预报", ("weather forecasting", "numerical weather prediction")),
        )),
    ),

    # ========== 人文与艺术 ==========
    "人文与艺术": (
        Subfield("文学与语言学", ("cs.CL",), ("linguistics", "literature"), (
            ("计算语言学", ("computational linguistics",)),
            ("数字人文", ("digital humanities",)),
            ("文本挖掘", ("text mining",)),
            ("语料库研究", ("corpus linguistics",)),
        )),
        Subfield("历史与考古", (), ("history", "archaeology"), (
            ("数字人文", ("digital humanities",)),
            ("文化遗产保护", ("cultural heritage preservation",)),
            ("考古遥感", ("archaeological remote sensing",)),
            ("历史文本分析", ("historical text analysis",)),
        )),
        Subfield("艺术与设计", (), ("art", "design"), (
            ("AI 生成艺术", ("AI generated art",)),
            ("人机共创", ("human-AI co-creation",)),
            ("交互设计", ("interaction design",)),
            ("数字策展", ("digital curation",)),
        )),
        Subfield("哲学与伦理", (), ("philosophy", "ethics"), (
            ("AI 伦理", ("AI ethics",)),
            ("算法治理", ("algorithmic governance",)),
            ("心智哲学", ("philosophy of mind",)),
            ("技术哲学", ("philosophy of technology",)),
        )),
        Subfield("音乐与表演艺术", (), ("music", "performing arts"), (
            ("音乐生成", ("music generation",)),
            ("计算音乐学", ("computational musicology",)),
            ("表演分析", ("performance analysis",)),
            ("数字乐器", ("digital musical instrument",)),
        )),
        Subfield("宗教与文化研究", (), ("religious studies", "cultural studies"), (
            ("数字宗教", ("digital religion",)),
            ("文化计算", ("computational culture",)),
            ("话语分析", ("discourse analysis",)),
            ("跨文化研究", ("cross-cultural studies",)),
        )),
    ),
}

# 三级学科：按 (门类, 二级学科) 归档
CHILDREN: dict[tuple[str, str], tuple[Subfield, ...]] = {
    # ---- 计算机科学 ----
    ("计算机科学", "人工智能"): (
        Subfield("大模型", ("cs.CL", "cs.AI"), ("language model",), (
            ("大模型推理", ("large language model reasoning", "chain of thought")),
            ("长上下文", ("long context modeling", "long context LLM")),
            ("多模态大模型", ("multimodal large language model",)),
        )),
        Subfield("生成模型", ("cs.CV", "cs.LG"), ("generative model",), (
            ("扩散模型", ("diffusion model", "text-to-image generation")),
            ("视频生成", ("video generation", "text-to-video")),
            ("图像编辑", ("image editing", "diffusion editing")),
        )),
        Subfield("强化学习与对齐", ("cs.LG",), ("reinforcement learning",), (
            ("强化学习", ("reinforcement learning",)),
            ("RLHF", ("RLHF", "human feedback")),
            ("智能体训练", ("agent training", "tool-use RL")),
        )),
    ),
    ("计算机科学", "自然语言处理"): (
        Subfield("信息检索", ("cs.IR",), ("information retrieval",), (
            ("检索增强生成", ("retrieval augmented generation", "RAG")),
            ("稠密检索", ("dense retrieval", "neural retrieval")),
            ("重排序", ("learning to rank", "reranking")),
        )),
        Subfield("机器翻译与多语言", ("cs.CL",), ("machine translation",), (
            ("机器翻译", ("machine translation",)),
            ("多语言模型", ("multilingual language model",)),
            ("低资源语言", ("low-resource languages",)),
        )),
        Subfield("对话与推理", ("cs.CL",), ("dialogue",), (
            ("对话系统", ("dialogue systems",)),
            ("思维链", ("chain of thought", "reasoning")),
            ("工具调用", ("tool use", "function calling")),
        )),
    ),
    ("计算机科学", "计算机视觉"): (
        Subfield("生成式视觉", ("cs.CV",), ("generative vision",), (
            ("视频生成", ("video generation",)),
            ("图像编辑", ("image editing",)),
            ("三维生成", ("3D generation", "text-to-3D")),
        )),
        Subfield("三维视觉", ("cs.CV",), ("3D vision",), (
            ("三维重建", ("3D reconstruction",)),
            ("点云处理", ("point cloud processing",)),
            ("SLAM", ("SLAM", "visual localization")),
        )),
        Subfield("视频理解", ("cs.CV",), ("video understanding",), (
            ("动作识别", ("action recognition",)),
            ("视频问答", ("video question answering",)),
            ("时序定位", ("temporal grounding",)),
        )),
    ),
    ("计算机科学", "机器学习"): (
        Subfield("深度学习理论", ("cs.LG",), ("deep learning theory",), (
            ("泛化理论", ("generalization theory",)),
            ("优化动力学", ("optimization dynamics",)),
            ("缩放定律", ("scaling laws",)),
        )),
        Subfield("图学习", ("cs.LG",), ("graph learning",), (
            ("图神经网络", ("graph neural network",)),
            ("图表示学习", ("graph representation learning",)),
            ("知识图谱推理", ("knowledge graph reasoning",)),
        )),
        Subfield("自监督与表征学习", ("cs.LG",), ("self-supervised learning",), (
            ("对比学习", ("contrastive learning",)),
            ("掩码建模", ("masked modeling",)),
            ("迁移与元学习", ("transfer learning", "meta-learning")),
        )),
    ),
    ("计算机科学", "软件工程"): (
        Subfield("代码智能", ("cs.SE",), ("code",), (
            ("代码生成", ("code generation", "code LLM")),
            ("程序修复", ("automated program repair", "bug fixing")),
            ("代码评审", ("code review automation",)),
        )),
        Subfield("软件测试", ("cs.SE",), ("software testing",), (
            ("测试生成", ("test generation",)),
            ("模糊测试", ("fuzzing",)),
            ("缺陷预测", ("defect prediction",)),
        )),
        Subfield("程序分析与验证", ("cs.SE", "cs.PL"), ("program analysis",), (
            ("静态分析", ("static analysis",)),
            ("形式验证", ("formal verification",)),
            ("程序合成", ("program synthesis",)),
        )),
    ),
    ("计算机科学", "安全与隐私"): (
        Subfield("大模型安全", ("cs.CR",), ("LLM security",), (
            ("越狱攻击", ("LLM jailbreak",)),
            ("提示注入", ("prompt injection",)),
            ("对齐失效", ("alignment failure", "reward hacking")),
        )),
        Subfield("隐私计算", ("cs.CR",), ("privacy",), (
            ("联邦学习", ("federated learning",)),
            ("差分隐私", ("differential privacy",)),
            ("同态加密", ("homomorphic encryption",)),
        )),
        Subfield("系统与网络安全", ("cs.CR",), ("network security",), (
            ("漏洞挖掘", ("vulnerability discovery",)),
            ("供应链安全", ("software supply chain security",)),
            ("入侵检测", ("intrusion detection",)),
        )),
    ),
    ("计算机科学", "数据库与数据管理"): (
        Subfield("向量数据库与检索", ("cs.DB", "cs.IR"), ("vector database",), (
            ("向量数据库", ("vector database",)),
            ("近似最近邻", ("approximate nearest neighbor",)),
            ("混合检索", ("hybrid search",)),
        )),
        Subfield("查询处理与优化", ("cs.DB",), ("query processing",), (
            ("查询优化", ("query optimization",)),
            ("执行引擎", ("query execution engine",)),
            ("事务与并发", ("transaction processing",)),
        )),
        Subfield("数据治理与质量", ("cs.DB",), ("data quality",), (
            ("数据清洗", ("data cleaning",)),
            ("数据湖", ("data lake", "lakehouse")),
            ("数据治理", ("data governance",)),
        )),
    ),
    ("计算机科学", "分布式与网络"): (
        Subfield("分布式训练", ("cs.DC",), ("distributed training",), (
            ("分布式训练", ("distributed training",)),
            ("通信优化", ("communication-efficient training",)),
            ("容错与检查点", ("fault tolerance", "checkpointing")),
        )),
        Subfield("云原生与调度", ("cs.DC",), ("cloud native",), (
            ("云原生调度", ("cloud native scheduling", "kubernetes")),
            ("算力优化", ("cluster scheduling",)),
            ("无服务器计算", ("serverless computing",)),
        )),
        Subfield("网络协议与低时延", ("cs.NI",), ("networking",), (
            ("低时延网络", ("low-latency networking",)),
            ("拥塞控制", ("congestion control",)),
            ("数据中心网络", ("data center network",)),
        )),
    ),
    ("计算机科学", "信息检索与推荐"): (
        Subfield("稠密检索", ("cs.IR",), ("dense retrieval",), (
            ("稠密检索", ("dense retrieval",)),
            ("多向量检索", ("multi-vector retrieval",)),
            ("跨模态检索", ("cross-modal retrieval",)),
        )),
        Subfield("推荐系统", ("cs.IR",), ("recommender system",), (
            ("协同过滤", ("collaborative filtering",)),
            ("序列推荐", ("sequential recommendation",)),
            ("图推荐", ("graph recommendation",)),
        )),
        Subfield("排序与评估", ("cs.IR",), ("learning to rank",), (
            ("重排序", ("reranking",)),
            ("离线评估", ("offline evaluation",)),
            ("大模型推荐", ("LLM-based recommendation",)),
        )),
    ),
    ("计算机科学", "人机交互"): (
        Subfield("交互设计与可用性", ("cs.HC",), ("interaction design",), (
            ("交互设计", ("interaction design",)),
            ("可用性评估", ("usability evaluation",)),
            ("用户研究", ("user study",)),
        )),
        Subfield("无障碍与包容性", ("cs.HC",), ("accessibility",), (
            ("无障碍技术", ("accessibility technology",)),
            ("辅助技术", ("assistive technology",)),
            ("包容性设计", ("inclusive design",)),
        )),
        Subfield("AR/VR 交互", ("cs.HC",), ("augmented reality",), (
            ("增强现实", ("augmented reality",)),
            ("虚拟现实交互", ("virtual reality interaction",)),
            ("沉浸式界面", ("immersive interface",)),
        )),
    ),
    ("计算机科学", "系统架构"): (
        Subfield("操作系统与虚拟化", ("cs.OS",), ("operating system",), (
            ("操作系统", ("operating system",)),
            ("虚拟化", ("virtualization",)),
            ("内存管理", ("memory management",)),
        )),
        Subfield("编译与性能优化", ("cs.PL", "cs.PF"), ("compiler",), (
            ("编译器优化", ("compiler optimization",)),
            ("程序性能分析", ("performance profiling",)),
            ("二进制翻译", ("binary translation",)),
        )),
        Subfield("异构与加速计算", ("cs.AR", "cs.DC"), ("heterogeneous computing",), (
            ("异构计算", ("heterogeneous computing",)),
            ("GPU 加速", ("GPU acceleration",)),
            ("存算一体", ("processing-in-memory",)),
        )),
    ),

    # ---- 工程学 ----
    ("工程学", "机器人学"): (
        Subfield("操作与抓取", ("cs.RO",), ("manipulation",), (
            ("灵巧操作", ("dexterous manipulation",)),
            ("抓取规划", ("grasp planning",)),
            ("双臂协作", ("bimanual manipulation",)),
        )),
        Subfield("移动机器人", ("cs.RO",), ("mobile robot",), (
            ("SLAM", ("SLAM",)),
            ("路径规划", ("path planning",)),
            ("四足机器人", ("legged robot",)),
        )),
        Subfield("具身智能", ("cs.RO", "cs.AI"), ("embodied AI",), (
            ("具身智能", ("embodied AI", "embodied agent")),
            ("视觉导航", ("visual navigation",)),
            ("世界模型", ("world model",)),
        )),
    ),
    ("工程学", "控制与自动化"): (
        Subfield("自动驾驶", ("cs.RO",), ("autonomous driving",), (
            ("自动驾驶", ("autonomous driving",)),
            ("感知决策", ("perception and planning",)),
            ("车路协同", ("vehicle-to-everything",)),
        )),
        Subfield("工业控制", ("eess.SY",), ("industrial control",), (
            ("模型预测控制", ("model predictive control",)),
            ("故障诊断", ("fault diagnosis",)),
            ("工业质检", ("industrial inspection",)),
        )),
        Subfield("多智能体控制", ("eess.SY",), ("multi-agent control",), (
            ("多智能体控制", ("multi-agent control",)),
            ("分布式优化", ("distributed optimization",)),
            ("编队协同", ("formation control",)),
        )),
    ),
    ("工程学", "工业工程"): (
        Subfield("智能制造", ("eess.SY",), ("smart manufacturing",), (
            ("智能制造", ("smart manufacturing", "smart factory")),
            ("数字孪生", ("digital twin",)),
            ("柔性产线", ("flexible production line",)),
        )),
        Subfield("生产与供应链优化", ("eess.SY",), ("production planning",), (
            ("生产调度", ("production scheduling",)),
            ("供应链优化", ("supply chain optimization",)),
            ("库存管理", ("inventory management",)),
        )),
        Subfield("质量与可靠性", ("eess.SY",), ("quality engineering",), (
            ("可靠性工程", ("reliability engineering",)),
            ("统计过程控制", ("statistical process control",)),
            ("预测性维护", ("predictive maintenance",)),
        )),
    ),
    ("工程学", "能源工程"): (
        Subfield("电化学储能", ("physics.app-ph",), ("electrochemical energy",), (
            ("储能技术", ("energy storage",)),
            ("固态电池", ("solid-state battery",)),
            ("电池管理", ("battery management",)),
        )),
        Subfield("可再生能源", ("physics.app-ph",), ("renewable energy",), (
            ("光伏电池", ("photovoltaic solar cell",)),
            ("风能", ("wind energy",)),
            ("氢能", ("hydrogen energy",)),
        )),
        Subfield("热管理与热力系统", ("physics.app-ph",), ("thermal management",), (
            ("热管理", ("thermal management",)),
            ("热泵与制冷", ("heat pump", "refrigeration")),
            ("燃烧与动力循环", ("combustion", "power cycle")),
        )),
    ),
    ("工程学", "航空航天"): (
        Subfield("航天器控制", ("eess.SY",), ("spacecraft control",), (
            ("航天器控制", ("spacecraft control",)),
            ("姿态确定", ("attitude determination",)),
            ("轨道设计", ("orbit design",)),
        )),
        Subfield("气动与结构", ("physics.flu-dyn",), ("aerodynamics",), (
            ("气动设计", ("aerodynamic design",)),
            ("结构动力学", ("structural dynamics",)),
            ("计算流体力学", ("computational fluid dynamics",)),
        )),
        Subfield("空间机器人与卫星", ("cs.RO",), ("space robotics",), (
            ("空间机器人", ("space robotics",)),
            ("卫星遥感", ("satellite remote sensing",)),
            ("低空经济", ("urban air mobility", "eVTOL")),
        )),
    ),
    ("工程学", "生物医学工程"): (
        Subfield("医学影像设备", ("eess.IV",), ("medical imaging",), (
            ("医学影像分析", ("medical imaging analysis",)),
            ("成像算法", ("image reconstruction",)),
            ("放射组学", ("radiomics",)),
        )),
        Subfield("神经接口", ("q-bio.NC",), ("neural interface",), (
            ("脑机接口", ("brain-computer interface",)),
            ("神经解码", ("neural decoding",)),
            ("电生理", ("electrophysiology",)),
        )),
        Subfield("可穿戴与生物传感", ("eess.SP",), ("wearable sensor",), (
            ("可穿戴传感", ("wearable sensors",)),
            ("生物力学", ("biomechanics",)),
            ("柔性生物电子", ("flexible bioelectronics",)),
        )),
    ),
    ("工程学", "化学工程"): (
        Subfield("催化与反应工程", ("physics.chem-ph",), ("catalysis",), (
            ("催化剂工程", ("catalyst engineering",)),
            ("反应器设计", ("reactor design",)),
            ("电催化", ("electrocatalysis",)),
        )),
        Subfield("过程系统工程", ("eess.SY",), ("process systems engineering",), (
            ("过程优化", ("process optimization",)),
            ("过程控制", ("process control",)),
            ("工艺建模", ("process modeling",)),
        )),
        Subfield("分离与绿色化工", ("physics.chem-ph",), ("separation technology",), (
            ("分离技术", ("separation technology",)),
            ("绿色化工", ("green chemical process",)),
            ("膜分离", ("membrane separation",)),
        )),
    ),
    ("工程学", "土木与交通"): (
        Subfield("智能交通", ("eess.SY",), ("intelligent transportation",), (
            ("智能交通", ("intelligent transportation system",)),
            ("交通流预测", ("traffic flow prediction",)),
            ("出行行为", ("travel behavior",)),
        )),
        Subfield("结构健康监测", ("eess.SP",), ("structural health monitoring",), (
            ("结构健康监测", ("structural health monitoring",)),
            ("震害评估", ("seismic damage assessment",)),
            ("桥梁监测", ("bridge monitoring",)),
        )),
        Subfield("建筑与地下工程", ("eess.SY",), ("civil engineering",), (
            ("建筑信息模型", ("building information modeling", "BIM")),
            ("地下工程", ("underground engineering", "tunneling")),
            ("绿色建筑", ("green building",)),
        )),
    ),
    ("工程学", "电子与电气工程"): (
        Subfield("电路与芯片设计", ("eess.SP",), ("circuit design",), (
            ("电路设计", ("circuit design", "VLSI")),
            ("芯片架构", ("chip architecture",)),
            ("低功耗设计", ("low-power design",)),
        )),
        Subfield("信号与通信", ("eess.SP",), ("signal processing",), (
            ("信号处理", ("signal processing",)),
            ("压缩感知", ("compressed sensing",)),
            ("无线通信", ("wireless communication",)),
        )),
        Subfield("传感与物联网", ("eess.SP",), ("sensor network",), (
            ("传感器", ("sensor", "IoT sensing")),
            ("物联网", ("internet of things",)),
            ("边缘计算", ("edge computing",)),
        )),
    ),

    # ---- 材料科学 ----
    ("材料科学", "超导与量子材料"): (
        Subfield("超导机理与材料", ("cond-mat.supr-con",), ("superconductivity",), (
            ("超导材料", ("superconductivity", "superconducting materials")),
            ("超导机理", ("superconducting mechanism",)),
            ("超导器件", ("superconducting device",)),
        )),
        Subfield("拓扑量子材料", ("cond-mat.mes-hall",), ("topological material",), (
            ("拓扑物态", ("topological states of matter",)),
            ("量子自旋霍尔", ("quantum spin Hall",)),
            ("莫尔超晶格", ("moiré superlattice",)),
        )),
    ),
    ("材料科学", "二维与低维材料"): (
        Subfield("石墨烯与二维材料", ("cond-mat.mes-hall",), ("two-dimensional materials",), (
            ("二维材料", ("two-dimensional materials",)),
            ("石墨烯", ("graphene",)),
            ("范德华异质结", ("van der Waals heterostructure",)),
        )),
        Subfield("量子点与纳米结构", ("cond-mat.mes-hall",), ("quantum dot",), (
            ("量子点", ("quantum dot",)),
            ("纳米材料", ("nanomaterial", "nanoparticle")),
            ("纳米线", ("nanowire",)),
        )),
    ),
    ("材料科学", "高熵与多主元合金"): (
        Subfield("高熵合金设计", ("cond-mat.mtrl-sci",), ("high-entropy alloy",), (
            ("高熵合金", ("high-entropy alloy",)),
            ("多主元合金", ("multi-principal element alloy",)),
            ("相稳定性", ("phase stability",)),
        )),
        Subfield("结构金属材料", ("cond-mat.mtrl-sci",), ("structural material",), (
            ("结构材料", ("structural material",)),
            ("金属间化合物", ("intermetallic compound",)),
            ("材料基因组", ("materials genome",)),
        )),
    ),
    ("材料科学", "能源材料"): (
        Subfield("电池与电解质", ("cond-mat.mtrl-sci",), ("battery materials",), (
            ("电池材料", ("battery materials", "electrode materials")),
            ("固态电解质", ("solid-state electrolyte",)),
            ("电极界面", ("electrode interface",)),
        )),
        Subfield("光伏与热电材料", ("cond-mat.mtrl-sci",), ("photovoltaic material",), (
            ("光伏材料", ("photovoltaic material", "perovskite")),
            ("热电器件", ("thermoelectric material",)),
            ("光催化", ("photocatalysis",)),
        )),
    ),
    ("材料科学", "计算材料学"): (
        Subfield("第一性原理", ("cond-mat.mtrl-sci",), ("first-principles",), (
            ("密度泛函理论", ("density functional theory", "DFT")),
            ("电子结构计算", ("electronic structure calculation",)),
            ("缺陷计算", ("defect calculation",)),
        )),
        Subfield("分子模拟与机器学习", ("cond-mat.mtrl-sci",), ("molecular dynamics",), (
            ("分子动力学", ("molecular dynamics simulation",)),
            ("高通量筛选", ("high-throughput screening",)),
            ("机器学习加速", ("machine learning for materials",)),
        )),
    ),
    ("材料科学", "智能材料与超材料"): (
        Subfield("超材料与超表面", ("cond-mat.mtrl-sci",), ("metamaterial",), (
            ("超材料", ("metamaterial",)),
            ("超表面", ("metasurface",)),
            ("拓扑声子", ("topological phononics",)),
        )),
        Subfield("形状记忆与自修复", ("cond-mat.mtrl-sci",), ("smart materials",), (
            ("智能材料", ("smart materials",)),
            ("形状记忆合金", ("shape memory alloy",)),
            ("自修复材料", ("self-healing material",)),
        )),
    ),

    # ---- 数学 ----
    ("数学", "基础数学"): (
        Subfield("数理逻辑", ("math.LO",), ("mathematical logic",), (
            ("定理证明", ("theorem proving",)),
            ("模型论", ("model theory",)),
            ("证明论", ("proof theory",)),
        )),
        Subfield("代数与几何", ("math.AG", "math.RT"), ("algebra", "geometry"), (
            ("代数几何", ("algebraic geometry",)),
            ("表示论", ("representation theory",)),
            ("数论", ("number theory",)),
        )),
        Subfield("组合数学", ("math.CO",), ("combinatorics",), (
            ("组合数学", ("combinatorics",)),
            ("极值组合", ("extremal combinatorics",)),
            ("代数组合", ("algebraic combinatorics",)),
        )),
    ),
    ("数学", "应用数学"): (
        Subfield("优化理论", ("math.OC",), ("optimization theory",), (
            ("随机优化", ("stochastic optimization",)),
            ("凸优化", ("convex optimization",)),
            ("最优控制", ("optimal control",)),
        )),
        Subfield("科学计算", ("math.NA",), ("scientific computing",), (
            ("数值计算", ("numerical analysis",)),
            ("反问题", ("inverse problems",)),
            ("高性能计算", ("high performance computing",)),
        )),
        Subfield("偏微分方程", ("math.AP",), ("partial differential equations",), (
            ("偏微分方程", ("partial differential equations",)),
            ("数值求解", ("numerical PDE",)),
            ("非线性分析", ("nonlinear analysis",)),
        )),
    ),
    ("数学", "统计学"): (
        Subfield("高维统计", ("stat.ME", "stat.TH"), ("high-dimensional statistics",), (
            ("高维统计", ("high-dimensional statistics",)),
            ("变量选择", ("variable selection",)),
            ("统计学习理论", ("statistical learning theory",)),
        )),
        Subfield("因果推断", ("stat.ME",), ("causal inference",), (
            ("因果推断", ("causal inference",)),
            ("工具变量", ("instrumental variable",)),
            ("半参数模型", ("semiparametric model",)),
        )),
        Subfield("贝叶斯统计", ("stat.ME",), ("Bayesian",), (
            ("贝叶斯方法", ("Bayesian methods",)),
            ("变分推断", ("variational inference",)),
            ("概率编程", ("probabilistic programming",)),
        )),
    ),
    ("数学", "概率论与随机过程"): (
        Subfield("随机过程与极限理论", ("math.PR",), ("stochastic processes",), (
            ("随机过程", ("stochastic processes",)),
            ("极限理论", ("limit theorems",)),
            ("大偏差理论", ("large deviations",)),
        )),
        Subfield("随机矩阵", ("math.PR",), ("random matrices",), (
            ("随机矩阵", ("random matrices",)),
            ("随机图", ("random graph",)),
            ("高维概率", ("high-dimensional probability",)),
        )),
    ),
    ("数学", "计算数学"): (
        Subfield("数值线性代数", ("math.NA",), ("numerical linear algebra",), (
            ("数值线性代数", ("numerical linear algebra",)),
            ("矩阵分解", ("matrix factorization",)),
            ("迭代求解", ("iterative solver",)),
        )),
        Subfield("有限元与谱方法", ("math.NA",), ("finite element method",), (
            ("有限元方法", ("finite element method",)),
            ("谱方法", ("spectral methods",)),
            ("间断伽辽金", ("discontinuous Galerkin",)),
        )),
    ),
    ("数学", "拓扑与几何"): (
        Subfield("代数拓扑", ("math.AT",), ("algebraic topology",), (
            ("代数拓扑", ("algebraic topology",)),
            ("同调论", ("homology theory",)),
            ("拓扑数据分析", ("topological data analysis",)),
        )),
        Subfield("微分几何与几何分析", ("math.DG",), ("differential geometry",), (
            ("微分几何", ("differential geometry",)),
            ("几何分析", ("geometric analysis",)),
            ("里奇流", ("Ricci flow",)),
        )),
    ),
    ("数学", "图论与组合优化"): (
        Subfield("图论", ("math.CO",), ("graph theory",), (
            ("图论", ("graph theory",)),
            ("极值图论", ("extremal graph theory",)),
            ("图谱理论", ("spectral graph theory",)),
        )),
        Subfield("组合优化与网络", ("math.OC", "cs.DM"), ("combinatorial optimization",), (
            ("组合优化", ("combinatorial optimization",)),
            ("网络流", ("network flow",)),
            ("近似算法", ("approximation algorithm",)),
        )),
    ),

    # ---- 物理与天文学 ----
    ("物理与天文学", "量子科学"): (
        Subfield("量子计算", ("quant-ph",), ("quantum computing",), (
            ("量子计算", ("quantum computing",)),
            ("量子算法", ("quantum algorithms",)),
            ("量子优越性", ("quantum advantage",)),
        )),
        Subfield("量子纠错与模拟", ("quant-ph",), ("quantum error correction",), (
            ("量子纠错", ("quantum error correction",)),
            ("量子模拟", ("quantum simulation",)),
            ("量子硬件", ("quantum hardware",)),
        )),
        Subfield("量子通信", ("quant-ph",), ("quantum communication",), (
            ("量子网络", ("quantum network",)),
            ("量子密钥分发", ("quantum key distribution",)),
            ("量子中继", ("quantum repeater",)),
        )),
    ),
    ("物理与天文学", "粒子物理"): (
        Subfield("对撞机物理", ("hep-ex",), ("collider",), (
            ("对撞机物理", ("collider physics",)),
            ("希格斯物理", ("Higgs boson",)),
            ("超出标准模型", ("beyond standard model",)),
        )),
        Subfield("中微子与暗物质", ("hep-ph",), ("neutrino",), (
            ("中微子物理", ("neutrino physics",)),
            ("暗物质探测", ("dark matter detection",)),
            ("宇宙线", ("cosmic rays",)),
        )),
    ),
    ("物理与天文学", "天体与宇宙学"): (
        Subfield("引力波与黑洞", ("gr-qc", "astro-ph"), ("gravitational waves",), (
            ("引力波", ("gravitational waves",)),
            ("黑洞物理", ("black hole physics",)),
            ("中子星", ("neutron star",)),
        )),
        Subfield("系外行星与宇宙学", ("astro-ph",), ("exoplanets",), (
            ("系外行星", ("exoplanets",)),
            ("宇宙学模拟", ("cosmological simulation",)),
            ("暗能量", ("dark energy",)),
        )),
    ),
    ("物理与天文学", "光学与光子学"): (
        Subfield("超快与量子光学", ("physics.optics",), ("ultrafast optics",), (
            ("超快激光", ("ultrafast laser",)),
            ("量子光学", ("quantum optics",)),
            ("非线性光学", ("nonlinear optics",)),
        )),
        Subfield("光学计算与光子集成", ("physics.optics",), ("photonic integrated circuit",), (
            ("光学计算", ("optical computing",)),
            ("光子集成", ("photonic integration",)),
            ("硅光子", ("silicon photonics",)),
        )),
    ),
    ("物理与天文学", "等离子体物理与聚变"): (
        Subfield("磁约束聚变", ("physics.plasm-ph",), ("magnetic confinement",), (
            ("聚变能", ("fusion energy", "tokamak")),
            ("等离子体约束", ("plasma confinement",)),
            ("托卡马克", ("tokamak",)),
        )),
        Subfield("惯性约束与空间等离子体", ("physics.plasm-ph",), ("inertial confinement fusion",), (
            ("惯性约束", ("inertial confinement fusion",)),
            ("空间等离子体", ("space plasma",)),
            ("激光等离子体", ("laser plasma",)),
        )),
    ),
    ("物理与天文学", "凝聚态物理"): (
        Subfield("超导与拓扑", ("cond-mat",), ("superconductivity",), (
            ("超导材料", ("superconductivity", "superconducting materials")),
            ("拓扑物态", ("topological states of matter",)),
            ("强关联体系", ("strongly correlated system",)),
        )),
        Subfield("低维与莫尔体系", ("cond-mat.mes-hall",), ("low-dimensional",), (
            ("二维材料", ("two-dimensional materials",)),
            ("莫尔超晶格", ("moiré superlattice",)),
            ("二维磁性", ("two-dimensional magnetism",)),
        )),
    ),
    ("物理与天文学", "生物物理"): (
        Subfield("单分子与细胞生物物理", ("physics.bio-ph",), ("single-molecule",), (
            ("单分子物理", ("single-molecule biophysics",)),
            ("细胞力学", ("cellular mechanics",)),
            ("生物分子机器", ("biomolecular machine",)),
        )),
        Subfield("计算生物物理", ("physics.bio-ph", "q-bio.BM"), ("computational biophysics",), (
            ("分子动力学", ("molecular dynamics",)),
            ("自由能计算", ("free energy calculation",)),
            ("蛋白折叠", ("protein folding",)),
        )),
    ),

    # ---- 化学 ----
    ("化学", "计算化学"): (
        Subfield("量子化学方法", ("physics.chem-ph",), ("quantum chemistry",), (
            ("密度泛函理论", ("density functional theory", "DFT")),
            ("量子化学", ("quantum chemistry",)),
            ("电子结构", ("electronic structure",)),
        )),
        Subfield("分子模拟与机器学习", ("physics.chem-ph",), ("molecular simulation",), (
            ("分子生成", ("molecule generation",)),
            ("分子动力学", ("molecular dynamics",)),
            ("机器学习势", ("machine learning potential",)),
        )),
    ),
    ("化学", "有机化学"): (
        Subfield("有机合成", ("chem-ph",), ("organic synthesis",), (
            ("有机合成", ("organic synthesis",)),
            ("绿色有机合成", ("green organic synthesis",)),
            ("药物中间体", ("pharmaceutical intermediate",)),
        )),
        Subfield("不对称催化", ("chem-ph",), ("asymmetric catalysis",), (
            ("不对称催化", ("asymmetric catalysis",)),
            ("手性合成", ("chiral synthesis",)),
            ("光催化有机反应", ("photocatalytic organic reaction",)),
        )),
    ),
    ("化学", "无机化学"): (
        Subfield("配位与金属有机", ("chem-ph",), ("coordination chemistry",), (
            ("配合物化学", ("coordination chemistry",)),
            ("金属有机框架", ("metal-organic framework", "MOF")),
            ("金属有机催化", ("organometallic catalysis",)),
        )),
        Subfield("固态与主族化学", ("chem-ph",), ("solid-state chemistry",), (
            ("固态化学", ("solid-state chemistry",)),
            ("主族元素化学", ("main group chemistry",)),
            ("无机材料合成", ("inorganic material synthesis",)),
        )),
    ),
    ("化学", "物理化学"): (
        Subfield("催化与界面", ("chem-ph",), ("catalysis",), (
            ("催化反应", ("catalysis", "heterogeneous catalysis")),
            ("界面化学", ("surface chemistry", "interface science")),
            ("反应动力学", ("chemical reaction dynamics",)),
        )),
        Subfield("电化学", ("chem-ph",), ("electrochemistry",), (
            ("电化学", ("electrochemistry",)),
            ("电催化", ("electrocatalysis",)),
            ("电池化学", ("battery chemistry",)),
        )),
    ),
    ("化学", "生物化学"): (
        Subfield("蛋白质与酶", ("q-bio.BM",), ("protein chemistry",), (
            ("蛋白质化学", ("protein chemistry",)),
            ("酶催化", ("enzyme catalysis", "enzymology")),
            ("蛋白质设计", ("protein design",)),
        )),
        Subfield("核酸化学", ("q-bio.BM",), ("nucleic acid",), (
            ("核酸化学", ("nucleic acid chemistry",)),
            ("RNA 结构", ("RNA structure",)),
            ("基因编辑化学", ("gene editing chemistry",)),
        )),
    ),
    ("化学", "绿色与环境化学"): (
        Subfield("绿色合成", ("chem-ph",), ("green chemistry",), (
            ("绿色化学", ("green chemistry", "sustainable chemistry")),
            ("催化剂设计", ("catalyst design",)),
            ("绿色溶剂", ("green solvent",)),
        )),
        Subfield("CO2 转化与环境修复", ("chem-ph",), ("CO2 utilization",), (
            ("碳化学", ("carbon chemistry", "CO2 utilization")),
            ("污染物降解", ("pollutant degradation",)),
            ("环境修复", ("environmental remediation",)),
        )),
    ),

    # ---- 医学 ----
    ("医学", "肿瘤学"): (
        Subfield("免疫治疗", ("q-bio.CB",), ("immunotherapy",), (
            ("肿瘤免疫", ("tumor immunology",)),
            ("免疫检查点", ("immune checkpoint",)),
            ("CAR-T", ("CAR-T",)),
        )),
        Subfield("精准诊疗", ("q-bio.TO",), ("precision oncology",), (
            ("癌症早筛", ("early cancer detection",)),
            ("靶向治疗", ("targeted therapy",)),
            ("空间转录组", ("spatial transcriptomics",)),
        )),
    ),
    ("医学", "神经科学"): (
        Subfield("神经退行疾病", ("q-bio.NC",), ("neurodegeneration",), (
            ("阿尔茨海默病", ("Alzheimer's disease",)),
            ("帕金森病", ("Parkinson's disease",)),
            ("神经炎症", ("neuroinflammation",)),
        )),
        Subfield("脑机接口与神经解码", ("q-bio.NC",), ("brain-computer interface",), (
            ("脑机接口", ("brain-computer interface",)),
            ("神经解码", ("neural decoding",)),
            ("类脑计算", ("neuromorphic computing",)),
        )),
    ),
    ("医学", "临床医学"): (
        Subfield("医学影像", ("eess.IV",), ("medical imaging",), (
            ("医学影像分析", ("medical imaging analysis",)),
            ("放射组学", ("radiomics",)),
            ("影像生物标志物", ("imaging biomarker",)),
        )),
        Subfield("临床信息学", ("q-bio.QM",), ("clinical informatics",), (
            ("电子病历", ("electronic health records",)),
            ("临床决策支持", ("clinical decision support",)),
            ("精准医疗", ("precision medicine", "personalized medicine")),
        )),
    ),
    ("医学", "药物研发"): (
        Subfield("分子设计", ("q-bio.BM",), ("molecular design",), (
            ("分子生成", ("molecule generation",)),
            ("药物发现", ("drug discovery",)),
            ("药物重定位", ("drug repurposing",)),
        )),
        Subfield("蛋白结构与成药性", ("q-bio.BM",), ("protein structure",), (
            ("蛋白结构预测", ("protein structure prediction",)),
            ("蛋白设计", ("protein design",)),
            ("成药性预测", ("druglikeness prediction",)),
        )),
    ),
    ("医学", "公共卫生"): (
        Subfield("流行病学", ("q-bio.PE",), ("epidemiology",), (
            ("流行病学", ("epidemiology",)),
            ("疾病监测", ("disease surveillance",)),
            ("传染病建模", ("infectious disease modeling",)),
        )),
        Subfield("疫苗与健康公平", ("q-bio.PE",), ("vaccine",), (
            ("疫苗开发", ("vaccine development", "mRNA vaccine")),
            ("健康公平", ("health equity",)),
            ("疫苗犹豫", ("vaccine hesitancy",)),
        )),
    ),
    ("医学", "免疫学"): (
        Subfield("免疫治疗与检查点", ("q-bio.CB",), ("immunotherapy",), (
            ("免疫治疗", ("immunotherapy",)),
            ("免疫检查点", ("immune checkpoint inhibitor",)),
            ("T 细胞工程", ("T cell engineering",)),
        )),
        Subfield("自身免疫与疫苗平台", ("q-bio.CB",), ("autoimmune",), (
            ("自身免疫病", ("autoimmune disease",)),
            ("疫苗平台", ("vaccine platform technology",)),
            ("免疫组学", ("immunomics",)),
        )),
    ),
    ("医学", "病理学与遗传学"): (
        Subfield("基因编辑与遗传病", ("q-bio.GN",), ("gene editing",), (
            ("基因编辑", ("CRISPR gene editing",)),
            ("遗传咨询", ("genetic counseling",)),
            ("罕见病", ("rare disease",)),
        )),
        Subfield("液体活检", ("q-bio.QM",), ("liquid biopsy",), (
            ("液体活检", ("liquid biopsy",)),
            ("循环肿瘤 DNA", ("circulating tumor DNA",)),
            ("病理图像分析", ("pathology image analysis",)),
        )),
    ),
    ("医学", "牙医学与口腔科学"): (
        Subfield("口腔材料与再生", ("q-bio.TO",), ("dental materials",), (
            ("口腔材料", ("dental materials",)),
            ("骨组织再生", ("bone tissue regeneration",)),
            ("种植修复", ("dental implant", "prosthodontics")),
        )),
        Subfield("口腔影像与 AI", ("eess.IV",), ("oral imaging",), (
            ("口腔AI", ("AI in dentistry",)),
            ("牙科影像分析", ("dental image analysis",)),
            ("数字化口腔", ("digital dentistry",)),
        )),
    ),

    # ---- 生物科学 ----
    ("生物科学", "基因组学"): (
        Subfield("单细胞与空间组学", ("q-bio.GN",), ("single-cell",), (
            ("单细胞测序", ("single-cell sequencing",)),
            ("空间转录组", ("spatial transcriptomics",)),
            ("多组学整合", ("multi-omics integration",)),
        )),
        Subfield("基因组编辑", ("q-bio.GN",), ("genome editing",), (
            ("基因编辑", ("CRISPR gene editing",)),
            ("碱基编辑", ("base editing",)),
            ("基因组注释", ("genome annotation",)),
        )),
    ),
    ("生物科学", "蛋白质与结构生物学"): (
        Subfield("结构预测与设计", ("q-bio.BM",), ("protein structure prediction",), (
            ("蛋白结构预测", ("protein structure prediction",)),
            ("蛋白质设计", ("protein design", "de novo protein")),
            ("分子对接", ("molecular docking",)),
        )),
        Subfield("冷冻电镜", ("q-bio.BM",), ("cryo-EM",), (
            ("冷冻电镜", ("cryo-EM",)),
            ("结构生物学", ("structural biology",)),
            ("单颗粒分析", ("single particle analysis",)),
        )),
    ),
    ("生物科学", "合成生物学"): (
        Subfield("基因线路", ("q-bio.SC",), ("genetic circuit",), (
            ("基因线路设计", ("genetic circuit design",)),
            ("底盘生物", ("chassis organism",)),
            ("生物计算", ("biological computation", "DNA computing")),
        )),
        Subfield("代谢工程", ("q-bio.SC",), ("metabolic engineering",), (
            ("代谢工程", ("metabolic engineering",)),
            ("发酵工程", ("fermentation engineering",)),
            ("细胞工厂", ("cell factory",)),
        )),
    ),
    ("生物科学", "微生物学"): (
        Subfield("微生物组", ("q-bio.MN",), ("microbiome",), (
            ("微生物组学", ("microbiome",)),
            ("宏基因组学", ("metagenomics",)),
            ("肠道菌群", ("gut microbiota",)),
        )),
        Subfield("耐药与噬菌体", ("q-bio.MN",), ("antibiotic resistance",), (
            ("细菌耐药", ("antibiotic resistance",)),
            ("噬菌体疗法", ("phage therapy",)),
            ("病原微生物", ("pathogen microbiology",)),
        )),
    ),
    ("生物科学", "细胞生物学"): (
        Subfield("细胞信号", ("q-bio.CB",), ("cell signaling",), (
            ("细胞信号转导", ("cell signaling", "signal transduction")),
            ("细胞周期", ("cell cycle",)),
            ("细胞成像", ("cell imaging",)),
        )),
        Subfield("干细胞与再生", ("q-bio.CB",), ("stem cell",), (
            ("干细胞", ("stem cell",)),
            ("诱导多能干细胞", ("induced pluripotent stem cell",)),
            ("类器官", ("organoid",)),
        )),
    ),
    ("生物科学", "生态学与进化"): (
        Subfield("生物多样性与保护", ("q-bio.PE",), ("biodiversity",), (
            ("生物多样性", ("biodiversity",)),
            ("物种保护", ("species conservation",)),
            ("入侵物种", ("invasive species",)),
        )),
        Subfield("系统发育与宏基因组", ("q-bio.PE",), ("phylogenetics",), (
            ("系统发育", ("phylogenetics", "molecular evolution")),
            ("宏基因组学", ("metagenomics",)),
            ("环境 DNA", ("environmental DNA",)),
        )),
    ),

    # ---- 经济与商业 ----
    ("经济与商业", "经济学"): (
        Subfield("劳动经济学", ("econ",), ("labor economics",), (
            ("劳动市场", ("labor market",)),
            ("技能溢价", ("skill premium",)),
            ("自动化就业", ("automation and employment",)),
        )),
        Subfield("行为经济学", ("econ",), ("behavioral economics",), (
            ("行为经济学", ("behavioral economics",)),
            ("有限理性", ("bounded rationality",)),
            ("助推", ("nudge",)),
        )),
        Subfield("发展经济学", ("econ",), ("development economics",), (
            ("数字经济", ("digital economy",)),
            ("产业政策", ("industrial policy",)),
            ("贫困与不平等", ("poverty and inequality",)),
        )),
    ),
    ("经济与商业", "金融学"): (
        Subfield("金融风险", ("q-fin.RM",), ("financial risk",), (
            ("金融风险", ("financial risk",)),
            ("系统性风险", ("systemic risk",)),
            ("压力测试", ("stress testing",)),
        )),
        Subfield("量化投资", ("q-fin.PM",), ("quantitative investing",), (
            ("资产定价", ("asset pricing",)),
            ("因子模型", ("factor model",)),
            ("高频交易", ("high-frequency trading",)),
        )),
    ),
    ("经济与商业", "管理学"): (
        Subfield("组织行为", ("econ.GN",), ("organizational behavior",), (
            ("组织行为", ("organizational behavior",)),
            ("团队协作", ("team collaboration",)),
            ("领导力", ("leadership",)),
        )),
        Subfield("运营与供应链", ("econ.GN",), ("operations management",), (
            ("供应链韧性", ("supply chain resilience",)),
            ("运营优化", ("operations optimization",)),
            ("平台治理", ("platform governance",)),
        )),
    ),
    ("经济与商业", "营销与消费者行为"): (
        Subfield("计算广告", ("econ.GN",), ("computational advertising",), (
            ("广告投放", ("computational advertising",)),
            ("推荐系统", ("recommender system",)),
            ("社交媒体营销", ("social media marketing",)),
        )),
        Subfield("消费者洞察", ("econ.GN",), ("consumer behavior",), (
            ("消费者洞察", ("consumer insight", "user research")),
            ("消费者行为", ("consumer behavior",)),
            ("定价策略", ("pricing strategy",)),
        )),
    ),
    ("经济与商业", "会计学与公司治理"): (
        Subfield("财务报告与审计", ("econ.GN",), ("financial reporting",), (
            ("财务报告", ("financial reporting",)),
            ("审计", ("auditing",)),
            ("盈余管理", ("earnings management",)),
        )),
        Subfield("ESG 披露", ("econ.GN",), ("ESG disclosure",), (
            ("ESG 披露", ("ESG disclosure",)),
            ("公司治理", ("corporate governance",)),
            ("可持续金融", ("sustainable finance",)),
        )),
    ),
    ("经济与商业", "创新与创业"): (
        Subfield("技术创新", ("econ",), ("technological innovation",), (
            ("技术创新", ("technological innovation",)),
            ("产业政策", ("industrial policy",)),
            ("专利分析", ("patent analysis",)),
        )),
        Subfield("创业融资", ("econ",), ("entrepreneurial finance",), (
            ("创业融资", ("entrepreneurial finance", "venture capital")),
            ("创业生态", ("entrepreneurial ecosystem",)),
            ("商业模式", ("business model",)),
        )),
    ),
    ("经济与商业", "计量经济学"): (
        Subfield("因果识别", ("econ.EM",), ("causal inference",), (
            ("双重差分", ("difference-in-differences",)),
            ("断点回归", ("regression discontinuity",)),
            ("合成控制", ("synthetic control",)),
        )),
        Subfield("面板与工具变量", ("econ.EM",), ("panel data",), (
            ("面板数据", ("panel data",)),
            ("工具变量", ("instrumental variable",)),
            ("机器学习计量", ("machine learning econometrics",)),
        )),
    ),

    # ---- 社会科学与心理学 ----
    ("社会科学与心理学", "社会学"): (
        Subfield("社会网络", ("cs.SI",), ("social network analysis",), (
            ("社会网络分析", ("social network analysis",)),
            ("网络科学", ("network science",)),
            ("社会资本", ("social capital",)),
        )),
        Subfield("社会分层与老龄化", (), ("social stratification",), (
            ("社会分层", ("social stratification",)),
            ("人口老龄化", ("population aging",)),
            ("算法偏见", ("algorithmic bias",)),
        )),
    ),
    ("社会科学与心理学", "政治学"): (
        Subfield("数字治理", (), ("digital governance",), (
            ("数字治理", ("digital governance",)),
            ("平台治理", ("platform governance",)),
            ("政策文本分析", ("policy text analysis",)),
        )),
        Subfield("选举与政策分析", (), ("election forecasting",), (
            ("选举预测", ("election forecasting",)),
            ("国际关系量化", ("quantitative international relations",)),
            ("公共政策评估", ("public policy evaluation",)),
        )),
    ),
    ("社会科学与心理学", "教育学"): (
        Subfield("教育技术", ("cs.CY",), ("educational technology",), (
            ("智能私教", ("intelligent tutoring system",)),
            ("学习分析", ("learning analytics",)),
            ("在线教育", ("online learning",)),
        )),
        Subfield("教育政策与评估", (), ("education policy",), (
            ("教育公平", ("education equity",)),
            ("教育评估", ("education assessment",)),
            ("教育测量", ("educational measurement",)),
        )),
    ),
    ("社会科学与心理学", "传播学"): (
        Subfield("计算传播", ("cs.SI",), ("computational communication",), (
            ("虚假信息", ("misinformation", "disinformation")),
            ("舆情分析", ("public opinion analysis",)),
            ("推荐算法影响", ("recommendation algorithm", "filter bubble")),
        )),
        Subfield("媒介与文化", (), ("media studies",), (
            ("媒介研究", ("media studies",)),
            ("数字媒体", ("digital media",)),
            ("内容分析", ("content analysis",)),
        )),
    ),
    ("社会科学与心理学", "心理学"): (
        Subfield("认知心理学", (), ("cognitive psychology",), (
            ("认知建模", ("cognitive modeling",)),
            ("记忆与注意", ("memory and attention",)),
            ("决策心理", ("decision making",)),
        )),
        Subfield("计算精神病学", (), ("computational psychiatry",), (
            ("心理健康", ("mental health",)),
            ("数字表型", ("digital phenotyping",)),
            ("心理测量", ("psychometrics",)),
        )),
    ),
    ("社会科学与心理学", "法学"): (
        Subfield("法律人工智能", ("cs.CY",), ("AI in law",), (
            ("法律人工智能", ("AI in law", "legal AI")),
            ("法律文本分析", ("legal text analysis",)),
            ("判例预测", ("case outcome prediction",)),
        )),
        Subfield("隐私与数据保护", (), ("privacy law",), (
            ("隐私与数据保护", ("privacy law", "data protection")),
            ("知识产权", ("intellectual property law",)),
            ("算法治理", ("algorithmic governance",)),
        )),
    ),
    ("社会科学与心理学", "人类学"): (
        Subfield("数字人类学", (), ("digital anthropology",), (
            ("数字人类学", ("digital anthropology",)),
            ("文化计算", ("computational culture",)),
            ("质性研究", ("qualitative research",)),
        )),
        Subfield("社会模拟", ("cs.SI",), ("agent-based model",), (
            ("社会模拟", ("social simulation", "agent-based model")),
            ("人类行为建模", ("human behavior modeling",)),
            ("计算社会科学", ("computational social science",)),
        )),
    ),

    # ---- 地球与环境科学 ----
    ("地球与环境科学", "气候科学"): (
        Subfield("极端气候", ("physics.ao-ph",), ("extreme climate",), (
            ("极端天气", ("extreme weather",)),
            ("气候模式", ("climate modeling",)),
            ("热浪", ("heatwave",)),
        )),
        Subfield("碳循环与碳中和", ("physics.ao-ph",), ("carbon cycle",), (
            ("碳循环", ("carbon cycle",)),
            ("碳中和", ("carbon neutrality",)),
            ("碳汇", ("carbon sink",)),
        )),
    ),
    ("地球与环境科学", "环境工程"): (
        Subfield("水处理与修复", (), ("water treatment",), (
            ("水处理", ("water treatment",)),
            ("环境修复", ("environmental remediation",)),
            ("污染物监测", ("pollutant monitoring",)),
        )),
        Subfield("大气污染控制", ("physics.ao-ph",), ("air pollution control",), (
            ("大气污染", ("air pollution",)),
            ("空气质量", ("air quality", "PM2.5")),
            ("碳捕集", ("carbon capture",)),
        )),
    ),
    ("地球与环境科学", "地球科学"): (
        Subfield("遥感与测绘", ("physics.geo-ph",), ("remote sensing",), (
            ("遥感监测", ("remote sensing",)),
            ("地表形变", ("surface deformation",)),
            ("地理信息", ("geographic information system",)),
        )),
        Subfield("地震与地质灾害", ("physics.geo-ph",), ("earthquake",), (
            ("地震预测", ("earthquake prediction",)),
            ("滑坡", ("landslide",)),
            ("地质建模", ("geological modeling",)),
        )),
    ),
    ("地球与环境科学", "生态学"): (
        Subfield("生物多样性与保护", ("q-bio.PE",), ("biodiversity",), (
            ("生物多样性", ("biodiversity",)),
            ("物种保护", ("species conservation",)),
            ("生态系统服务", ("ecosystem services",)),
        )),
        Subfield("生态系统建模", ("q-bio.PE",), ("ecosystem modeling",), (
            ("生态系统建模", ("ecosystem modeling",)),
            ("入侵物种", ("invasive species",)),
            ("生态遥感", ("ecological remote sensing",)),
        )),
    ),
    ("地球与环境科学", "海洋科学"): (
        Subfield("海洋环流与酸化", ("physics.ao-ph",), ("ocean circulation",), (
            ("海洋酸化", ("ocean acidification",)),
            ("海洋环流", ("ocean circulation",)),
            ("海平面上升", ("sea level rise",)),
        )),
        Subfield("珊瑚礁与海洋遥感", ("physics.ao-ph",), ("coral reefs",), (
            ("珊瑚礁", ("coral reefs",)),
            ("海洋遥感", ("ocean remote sensing",)),
            ("海洋生态", ("marine ecology",)),
        )),
    ),
    ("地球与环境科学", "能源与碳汇"): (
        Subfield("可再生能源系统", ("eess.SY",), ("renewable energy system",), (
            ("可再生能源", ("renewable energy",)),
            ("能源系统建模", ("energy system modeling",)),
            ("智能电网", ("smart grid",)),
        )),
        Subfield("碳汇与能源转型", ("physics.ao-ph",), ("carbon sink",), (
            ("碳汇", ("carbon sink", "forest carbon")),
            ("碳中和路径", ("carbon neutrality pathway",)),
            ("能源转型", ("energy transition",)),
        )),
    ),
    ("地球与环境科学", "大气科学"): (
        Subfield("大气污染与空气质量", ("physics.ao-ph",), ("air pollution",), (
            ("大气污染", ("air pollution",)),
            ("空气质量", ("air quality", "PM2.5")),
            ("污染传输", ("pollutant transport",)),
        )),
        Subfield("大气化学与数值预报", ("physics.ao-ph",), ("atmospheric chemistry",), (
            ("大气化学", ("atmospheric chemistry",)),
            ("天气预报", ("weather forecasting", "numerical weather prediction")),
            ("气溶胶", ("aerosol",)),
        )),
    ),

    # ---- 人文与艺术 ----
    ("人文与艺术", "文学与语言学"): (
        Subfield("计算语言学", ("cs.CL",), ("computational linguistics",), (
            ("计算语言学", ("computational linguistics",)),
            ("语料库研究", ("corpus linguistics",)),
            ("文本挖掘", ("text mining",)),
        )),
        Subfield("数字人文", (), ("digital humanities",), (
            ("数字人文", ("digital humanities",)),
            ("文学计算", ("computational literary studies",)),
            ("历史文本分析", ("historical text analysis",)),
        )),
    ),
    ("人文与艺术", "历史与考古"): (
        Subfield("数字考古", (), ("archaeology",), (
            ("考古遥感", ("archaeological remote sensing",)),
            ("数字考古", ("digital archaeology",)),
            ("文化遗产保护", ("cultural heritage preservation",)),
        )),
        Subfield("历史文本分析", (), ("history",), (
            ("历史文本分析", ("historical text analysis",)),
            ("数字史学", ("digital history",)),
            ("史料数字化", ("archival digitization",)),
        )),
    ),
    ("人文与艺术", "艺术与设计"): (
        Subfield("AI 生成艺术", ("cs.CV",), ("AI generated art",), (
            ("AI 生成艺术", ("AI generated art",)),
            ("人机共创", ("human-AI co-creation",)),
            ("生成设计", ("generative design",)),
        )),
        Subfield("交互与数字策展", ("cs.HC",), ("interaction design",), (
            ("交互设计", ("interaction design",)),
            ("数字策展", ("digital curation",)),
            ("数字乐器", ("digital musical instrument",)),
        )),
    ),
    ("人文与艺术", "哲学与伦理"): (
        Subfield("AI 伦理", (), ("AI ethics",), (
            ("AI 伦理", ("AI ethics",)),
            ("算法治理", ("algorithmic governance",)),
            ("价值对齐", ("value alignment",)),
        )),
        Subfield("技术哲学", (), ("philosophy of technology",), (
            ("技术哲学", ("philosophy of technology",)),
            ("心智哲学", ("philosophy of mind",)),
            ("意识研究", ("consciousness studies",)),
        )),
    ),
    ("人文与艺术", "音乐与表演艺术"): (
        Subfield("音乐生成", ("cs.SD",), ("music generation",), (
            ("音乐生成", ("music generation",)),
            ("数字乐器", ("digital musical instrument",)),
            ("音频处理", ("audio processing",)),
        )),
        Subfield("计算音乐学", ("cs.SD",), ("computational musicology",), (
            ("计算音乐学", ("computational musicology",)),
            ("表演分析", ("performance analysis",)),
            ("音乐信息检索", ("music information retrieval",)),
        )),
    ),
    ("人文与艺术", "宗教与文化研究"): (
        Subfield("数字宗教", (), ("digital religion",), (
            ("数字宗教", ("digital religion",)),
            ("宗教文本分析", ("religious text analysis",)),
            ("仪式研究", ("ritual studies",)),
        )),
        Subfield("文化计算与话语分析", (), ("computational culture",), (
            ("文化计算", ("computational culture",)),
            ("话语分析", ("discourse analysis",)),
            ("跨文化研究", ("cross-cultural studies",)),
        )),
    ),
}

# arXiv 分类前缀 → 学科门类（按顺序匹配）
_ARXIV_FIELDS: tuple[tuple[str, str], ...] = (
    ("cs.", "计算机科学"),
    ("eess.", "工程学"),
    ("cond-mat.mtrl-sci", "材料科学"),
    ("cond-mat", "物理与天文学"),
    ("physics.ao-ph", "地球与环境科学"),
    ("physics.geo-ph", "地球与环境科学"),
    ("physics.plasm-ph", "物理与天文学"),
    ("physics.bio-ph", "物理与天文学"),
    ("physics.phot-ph", "物理与天文学"),
    ("physics.optics", "物理与天文学"),
    ("physics.comp-ph", "化学"),
    ("physics.app-ph", "工程学"),
    ("physics.soc-ph", "社会科学与心理学"),
    ("physics.", "物理与天文学"),
    ("quant-ph", "物理与天文学"),
    ("astro-ph", "物理与天文学"),
    ("hep-", "物理与天文学"),
    ("nucl-", "物理与天文学"),
    ("gr-qc", "物理与天文学"),
    ("nlin", "物理与天文学"),
    ("math.", "数学"),
    ("stat.", "数学"),
    ("q-bio.SC", "生物科学"),
    ("q-bio.GN", "生物科学"),
    ("q-bio.QM", "生物科学"),
    ("q-bio.MN", "生物科学"),
    ("q-bio.IB", "生物科学"),
    ("q-bio.PE", "生物科学"),
    ("q-bio.CB", "生物科学"),
    ("q-bio.BM", "生物科学"),
    ("q-bio.NC", "生物科学"),
    ("q-bio.TO", "医学"),
    ("q-bio", "医学"),
    ("q-fin", "经济与商业"),
    ("econ", "经济与商业"),
    ("chem-ph", "化学"),
)

# Semantic Scholar 的 fieldsOfStudy 直接映射
_S2_FIELDS: dict[str, str] = {
    "computer science": "计算机科学",
    "engineering": "工程学",
    "materials science": "材料科学",
    "mathematics": "数学",
    "physics": "物理与天文学",
    "chemistry": "化学",
    "medicine": "医学",
    "dentistry": "医学",
    "nursing": "医学",
    "neuroscience": "生物科学",
    "biology": "生物科学",
    "biochemistry": "生物科学",
    "immunology": "生物科学",
    "genetics": "生物科学",
    "economics": "经济与商业",
    "business": "经济与商业",
    "sociology": "社会科学与心理学",
    "political science": "社会科学与心理学",
    "psychology": "社会科学与心理学",
    "education": "社会科学与心理学",
    "law": "社会科学与心理学",
    "anthropology": "社会科学与心理学",
    "environmental science": "地球与环境科学",
    "geology": "地球与环境科学",
    "geography": "地球与环境科学",
    "agricultural and biological sciences": "生物科学",
    "astronomy": "物理与天文学",
    "art": "人文与艺术",
    "history": "人文与艺术",
    "philosophy": "人文与艺术",
    "linguistics": "人文与艺术",
}

# Crossref 的 subject 常是组合词（如 "Computer Science Applications"），用子串提示兜底。
_CATEGORY_HINTS: tuple[tuple[str, str], ...] = (
    ("computer science", "计算机科学"),
    ("artificial intelligence", "计算机科学"),
    ("machine learning", "计算机科学"),
    ("software", "计算机科学"),
    ("information systems", "计算机科学"),
    ("engineering", "工程学"),
    ("mechanical engineering", "工程学"),
    ("electrical engineering", "工程学"),
    ("chemical engineering", "工程学"),
    ("civil engineering", "工程学"),
    ("materials science", "材料科学"),
    ("mathematics", "数学"),
    ("statistics", "数学"),
    ("physics", "物理与天文学"),
    ("astronomy", "物理与天文学"),
    ("chemistry", "化学"),
    ("organic chemistry", "化学"),
    ("inorganic chemistry", "化学"),
    ("physical chemistry", "化学"),
    ("medicine", "医学"),
    ("nursing", "医学"),
    ("dentistry", "医学"),
    ("pharmacology", "医学"),
    ("biology", "生物科学"),
    ("neuroscience", "生物科学"),
    ("biochemistry", "生物科学"),
    ("immunology", "生物科学"),
    ("microbiology", "生物科学"),
    ("genetics", "生物科学"),
    ("ecology", "生物科学"),
    ("economics", "经济与商业"),
    ("business", "经济与商业"),
    ("finance", "经济与商业"),
    ("management", "经济与商业"),
    ("accounting", "经济与商业"),
    ("marketing", "经济与商业"),
    ("sociology", "社会科学与心理学"),
    ("psychology", "社会科学与心理学"),
    ("political", "社会科学与心理学"),
    ("education", "社会科学与心理学"),
    ("law", "社会科学与心理学"),
    ("anthropology", "社会科学与心理学"),
    ("social", "社会科学与心理学"),
    ("environmental", "地球与环境科学"),
    ("geograph", "地球与环境科学"),
    ("geolog", "地球与环境科学"),
    ("ecolog", "生物科学"),
    ("ocean", "地球与环境科学"),
    ("atmosphere", "地球与环境科学"),
    ("climate", "地球与环境科学"),
    ("energy", "地球与环境科学"),
    ("art", "人文与艺术"),
    ("histor", "人文与艺术"),
    ("philosoph", "人文与艺术"),
    ("literature", "人文与艺术"),
    ("music", "人文与艺术"),
    ("linguistics", "人文与艺术"),
)

_VENUE_FIELDS: tuple[tuple[str, str], ...] = (
    (r"neurips|icml|iclr|cvpr|iccv|acl|emnlp|naacl|kdd|www|chi|uist|osdi|sosp|sigcomm|aaai|ijcai", "计算机科学"),
    (r"nature medicine|lancet|jama|nejm|cell|biorxiv|medrxiv", "医学"),
    (r"nature chemistry|journal of the american chemical society|angewandte", "化学"),
    (r"nature materials|advanced materials", "材料科学"),
    (r"physical review|nature physics|prl", "物理与天文学"),
    (r"nature neuroscience|neuron|journal of neuroscience", "生物科学"),
    (r"econometrica|journal of finance|nber", "经济与商业"),
    (r"nature climate change|nature sustainability", "地球与环境科学"),
)

_KEYWORDS: dict[str, tuple[str, ...]] = {
    "计算机科学": (
        "llm", "language model", "agent", "machine learning", "deep learning",
        "neural network", "software", "algorithm", "benchmark", "dataset",
        "retrieval", "computer vision", "nlp", "reasoning",
    ),
    "工程学": (
        "robot", "control system", "circuit", "energy", "manufacturing",
        "aerospace", "mechanical", "sensor",
    ),
    "材料科学": (
        "material", "superconduct", "catalyst", "alloy", "nanomaterial",
        "graphene", "perovskite", "MOF",
    ),
    "数学": (
        "theorem", "proof", "optimization", "probability", "statistic",
        "theorem proving", "proof assistant", "formal verification",
        "coq", "isabelle", "symbolic", "math word problem", "olympiad", "geometry",
    ),
    "物理与天文学": ("quantum", "particle", "cosmolog", "thermodynamic", "photon", "gravitational wave"),
    "化学": ("molecule", "catalyst", "reaction", "organic", "synthesis", "density functional", "DFT"),
    "医学": (
        "clinical", "patient", "disease", "medical", "drug", "health",
        "cancer", "tumor", "immunotherapy",
    ),
    "生物科学": (
        "gene", "protein", "cell", "biolog", "genome", "DNA", "RNA",
        "microbiome", "metabolome", "synthetic biology",
    ),
    "经济与商业": ("finance", "economic", "market", "pricing", "business", "management", "firm"),
    "社会科学与心理学": ("social", "education", "policy", "law", "psycholog", "survey", "behavior"),
    "地球与环境科学": ("climate", "ecolog", "geolog", "environment", "ocean", "carbon", "atmosphere"),
    "人文与艺术": ("histor", "philosoph", "literature", "art", "music", "culture", "heritage"),
}


def _count(text: str, token: str) -> int:
    if " " in token:
        return text.count(token)
    return len(re.findall(rf"\b{re.escape(token)}\b", text))


def _field_hits(paper: Paper) -> list[str]:
    """按 源分类 > venue > 关键词 收集门类，保留命中顺序，去重。"""
    ordered: list[str] = []

    def add(field: str) -> None:
        if field and field not in ordered:
            ordered.append(field)

    for raw in paper.categories:
        category = (raw or "").strip().lower()
        if not category:
            continue
        if category in _S2_FIELDS:
            add(_S2_FIELDS[category])
            continue
        for hint, field in _CATEGORY_HINTS:
            if hint in category:
                add(field)
                break
        for prefix, field in _ARXIV_FIELDS:
            if category.startswith(prefix):
                add(field)
                break

    venue = (paper.venue or "").lower()
    for pattern, field in _VENUE_FIELDS:
        if re.search(pattern, venue):
            add(field)

    if ordered:
        return ordered

    text = f"{paper.title} {paper.abstract}".lower()
    best_field, best_score = "", 0
    for field, tokens in _KEYWORDS.items():
        score = sum(_count(text, token) for token in tokens)
        if score > best_score:
            best_field, best_score = field, score
    return [best_field or "其他"]


def paper_fields(paper: Paper) -> set[str]:
    """返回论文命中的全部门类，交叉列表的论文会同时命中多个门类。"""
    return set(_field_hits(paper))


def classify_field(paper: Paper) -> str:
    """返回主门类，供报告聚合与展示。"""
    return _field_hits(paper)[0]


def subfields_of(field: str) -> tuple[Subfield, ...]:
    return tuple(SUBFIELDS.get(field, ()))


def children_of(field: str, name: str) -> tuple[Subfield, ...]:
    return CHILDREN.get((field, name), ())


def find_node(field: str, names: tuple[str, ...]) -> Subfield | None:
    """按 二级 → 三级 的层级顺序查找节点，任意一层不存在即返回 None。"""
    nodes = subfields_of(field)
    node: Subfield | None = None
    for name in names:
        node = next((item for item in nodes if item.name == name), None)
        if node is None:
            return None
        nodes = children_of(field, node.name)
    return node


def find_subfield(field: str, name: str) -> Subfield | None:
    return find_node(field, (name,))


def node_hotspots(field: str, names: tuple[str, ...]) -> list[str]:
    node = find_node(field, names)
    return [label for label, _ in node.hotspots] if node else []


def subfield_hotspots(field: str, name: str) -> list[str]:
    return node_hotspots(field, (name,))


def hotspot_terms() -> dict[str, list[str]]:
    """收集各层级热点主题的中文标签 → 英文检索词，供 Planner 复用。"""
    terms: dict[str, list[str]] = {}
    groups = list(SUBFIELDS.values()) + list(CHILDREN.values())
    for nodes in groups:
        for node in nodes:
            for label, queries in node.hotspots:
                bucket = terms.setdefault(label, [])
                for query in queries:
                    if query not in bucket:
                        bucket.append(query)
    return terms


def paper_matches_subfield(
    paper: Paper, field: str, name: str, child: str | None = None
) -> bool:
    """按分类前缀、学科提示词、热点英文词判定论文是否属于所选学科节点。"""
    node = find_node(field, (name, child) if child else (name,))
    if node is None:
        return False
    categories = [(c or "").strip().lower() for c in paper.categories]
    for category in categories:
        if not category:
            continue
        if any(category.startswith(prefix.lower()) for prefix in node.prefixes):
            return True
        if any(hint.lower() in category for hint in node.hints):
            return True
    text = f"{paper.title} {paper.abstract}".lower()
    return any(term.lower() in text for _, queries in node.hotspots for term in queries)
