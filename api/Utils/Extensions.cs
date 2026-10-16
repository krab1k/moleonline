using Mole.API.Models;
using System;
using System.Collections.Generic;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Threading.Tasks;

namespace Mole.API.Utils
{
    public static class Extensions
    {
        /// <summary>
        /// Determines if collection is null or empty
        /// </summary>
        /// <typeparam name="T"></typeparam>
        /// <param name="enumerable"></param>
        /// <returns></returns>
        public static bool IsNullOrEmpty<T>(this IEnumerable<T> enumerable)
        {
            return enumerable == null || !enumerable.Any();
        }

        /// <summary>
        /// Zips the directory in memory, so that concurrent downloads do not share a temporary file
        /// </summary>
        /// <param name="path"></param>
        /// <returns>Zip archive content</returns>
        public static byte[] ZipDirectory(string path) {
            using (var stream = new MemoryStream())
            {
                ZipFile.CreateFromDirectory(path, stream);
                return stream.ToArray();
            }
        }


        /// <summary>
        /// Compress entire direct to the 
        /// </summary>
        /// <param name="path"></param>
        /// <param name="molecule">Path to the structure file, added to the archive</param>
        public static void ZipDirectories(string path, string molecule) {
            using (FileStream zipToOpen = new FileStream(Path.Combine(path, MoleApiFiles.Report), FileMode.Create))
            {
                using (ZipArchive archive = new ZipArchive(zipToOpen, ZipArchiveMode.Create))
                {
                    ZipArchiveEntry readmeEntry;
                    DirectoryInfo d = new DirectoryInfo(path);
                    foreach (var item in d.GetDirectories("*"))
                    {
                        FileInfo[] Files = item.GetFiles("*");
                        foreach (FileInfo file in Files)
                        {
                            readmeEntry = archive.CreateEntryFromFile(Path.Combine(path, item.Name, file.Name), Path.Combine(item.Name, file.Name));
                        }
                    }
                    archive.CreateEntryFromFile(molecule, Path.GetFileName(molecule));
                }                
            }

        }
    }
}
