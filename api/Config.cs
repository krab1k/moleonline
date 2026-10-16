using Microsoft.Extensions.Options;

namespace Mole.API;

public class Config
{
    public string WorkingDirectory { get; set; } = ""; // the paths are checked at startup (ConfigValidation)
    public string CSA { get; set; } = "";
    public int MaxConcurentComputations { get; set; }
    public int MaxComputationMinutes { get; set; } // one run (all its processes), the queue not included
    public string MOLE { get; set; } = "";
    public string Pores { get; set; } = "";
}

/// <summary>
/// Checked at startup, so that a wrong path stops the API with a clear message instead of failing the first computation
/// </summary>
public class ConfigValidation : IValidateOptions<Config>
{
    public ValidateOptionsResult Validate(string? name, Config config)
    {
        List<string> errors = [];

        if (string.IsNullOrEmpty(config.WorkingDirectory)) errors.Add("Config:WorkingDirectory is not set.");
        else
        {
            try
            {
                Directory.CreateDirectory(config.WorkingDirectory);
                var probe = Path.Combine(config.WorkingDirectory, $".write-test-{Guid.NewGuid():N}");
                File.WriteAllText(probe, "");
                File.Delete(probe);
            }
            catch (Exception e) when (e is IOException or UnauthorizedAccessException)
            {
                errors.Add($"Config:WorkingDirectory '{config.WorkingDirectory}' is not writable: {e.Message}");
            }
        }

        if (!File.Exists(config.CSA)) errors.Add($"Config:CSA '{config.CSA}' does not exist.");
        if (config.MaxConcurentComputations < 1) errors.Add("Config:MaxConcurentComputations must be at least 1.");
        if (config.MaxComputationMinutes < 1) errors.Add("Config:MaxComputationMinutes must be at least 1.");
        CheckExecutable(errors, "MOLE", config.MOLE);
        CheckExecutable(errors, "Pores", config.Pores);

        return errors.Count == 0 ? ValidateOptionsResult.Success : ValidateOptionsResult.Fail(errors);
    }

    private static void CheckExecutable(List<string> errors, string setting, string path)
    {
        const UnixFileMode execute = UnixFileMode.UserExecute | UnixFileMode.GroupExecute | UnixFileMode.OtherExecute;
        if (!Path.IsPathFullyQualified(path ?? "")) errors.Add($"Config:{setting} '{path}' must be a full path, executables are not looked up in PATH.");
        else if (!File.Exists(path)) errors.Add($"Config:{setting} '{path}' does not exist.");
        else if ((File.GetUnixFileMode(path) & execute) == 0) errors.Add($"Config:{setting} '{path}' is not executable.");
    }
}
