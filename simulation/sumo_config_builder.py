import os


def build_sumocfg(net_file: str, rou_file: str, out_dir: str, end_time: int = 1200) -> str:
    os.makedirs(out_dir, exist_ok=True)
    cfg_path = os.path.join(out_dir, "scenario.sumocfg")

    content = f"""<configuration>
    <input>
        <net-file value="{os.path.basename(net_file)}"/>
        <route-files value="{os.path.basename(rou_file)}"/>
    </input>
    <time>
        <begin value="0"/>
        <end value="{end_time}"/>
        <step-length value="1"/>
    </time>
    <report>
        <no-step-log value="true"/>
        <duration-log.disable value="true"/>
    </report>
</configuration>
"""
    with open(cfg_path, "w", encoding="utf-8") as f:
        f.write(content)

    return cfg_path
